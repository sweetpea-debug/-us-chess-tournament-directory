#!/usr/bin/env python3
"""
Daily ingest for chess tournaments.

TEST MODE (as requested):
- Only pulls from:
  1) US Chess Upcoming Tournaments (new.uschess.org/upcoming-tournaments)
  2) Michigan Chess Association events (michess.org/events + event details)
- Emits only "real" events (must have a parseable date)
- Filters out past events (endDate < today)
- Writes events.json in repo root: { syncedAt, events }
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ----------------------------
# Paths
# ----------------------------

HERE = Path(__file__).resolve()
# If this file is scripts/build_events.py, repo root is parent of scripts/
ROOT = HERE.parents[1] if HERE.parent.name == "scripts" else HERE.parent
OUTPUT_PATH = ROOT / "events.json"


# ----------------------------
# Sources (ONLY these two)
# ----------------------------

SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "uschess-upcoming",
        "name": "US Chess Upcoming Tournaments",
        "parser": "uschess_upcoming",
        "endpoint": "https://new.uschess.org/upcoming-tournaments",
        "homepage": "https://new.uschess.org/upcoming-tournaments",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "parser": "michess_events",
        "endpoint": "https://www.michess.org/events",
        "homepage": "https://www.michess.org/events",
    },
]


# ----------------------------
# HTTP
# ----------------------------

DEFAULT_TIMEOUT_SECS = 25
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0; +https://github.com/)"

def fetch_text(url: str) -> str:
    """
    Basic fetch helper. Raises on non-200 responses.
    """
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECS) as resp:
            raw = resp.read()
            # Try UTF-8 first; fall back if needed.
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return raw.decode(errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"Fetch failed for {url}: {e}") from e


# ----------------------------
# Text extraction helpers
# ----------------------------

def _strip_html_to_lines(markup: str) -> list[str]:
    """
    Convert HTML into a list of cleaned text lines.
    This is intentionally lightweight (no external deps).
    """
    # Drop scripts/styles
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    # Turn common block ends into newlines
    markup = re.sub(r"</(p|div|li|h1|h2|h3|h4|tr|td|th|section|article|header|footer)\s*>", "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    # Drop remaining tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    # Normalize whitespace into clean lines
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"


def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Dedupe by (name + startDate + city + state) to avoid repeats.
    Prefer the first occurrence.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('name','')}|{e.get('startDate','')}|{e.get('city','')}|{e.get('state','')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def is_upcoming(event: dict[str, Any]) -> bool:
    """
    Keep events that end today or later.
    """
    today = date.today().isoformat()
    end_date = str(event.get("endDate") or "")
    return bool(end_date) and end_date >= today


# ----------------------------
# US Chess date parsing
# ----------------------------

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
}

def _parse_us_chess_date_one(s: str) -> date | None:
    """
    Parses: 'Wednesday, February 18, 2026' or 'February 18, 2026'
    """
    s = s.strip()
    s = re.sub(r"^[A-Za-z]+,\s*", "", s)  # remove weekday if present
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$", s)
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    day = int(m.group(2))
    yr = int(m.group(3))
    try:
        return date(yr, mon, day)
    except ValueError:
        return None


def _parse_us_chess_date_range(s: str) -> tuple[str, str] | None:
    """
    Parses date ranges like:
      'Saturday, January 3, 2026 - Sunday, January 4, 2026'
    or single dates like:
      'Wednesday, February 18, 2026'
    """
    s = s.strip()
    parts = [p.strip() for p in s.split(" - ")]
    if not parts:
        return None
    start = _parse_us_chess_date_one(parts[0])
    if not start:
        return None
    end = _parse_us_chess_date_one(parts[1]) if len(parts) > 1 else start
    if not end:
        end = start
    return (start.isoformat(), end.isoformat())


# ----------------------------
# Parser: US Chess Upcoming Tournaments
# ----------------------------

def parse_uschess_upcoming(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Lightweight heuristic scrape for US Chess upcoming tournaments page.

    This page may change layout; if it does, this parser may need adjustment.
    """
    lines = _strip_html_to_lines(page_html)

    out: list[dict[str, Any]] = []

    # We look for a 3-line pattern:
    #   Title
    #   City, ST ...
    #   Date line (parseable)
    #
    # This is intentionally conservative to avoid junk cards.
    for i in range(0, len(lines) - 3):
        title = lines[i].strip()
        loc = lines[i + 1].strip()
        date_line = lines[i + 2].strip()

        # Filter obvious non-titles
        if len(title) < 6:
            continue
        if title.lower() in {"upcoming tournaments", "tournaments", "events"}:
            continue

        loc_m = re.match(r"^(.+?),\s*([A-Z]{2})\b", loc)
        dr = _parse_us_chess_date_range(date_line)

        if not loc_m or not dr:
            continue

        city = loc_m.group(1).strip()
        state = loc_m.group(2).strip()
        startDate, endDate = dr

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": state,
            "venue": "See source listing",
            # No geocoding in this test. Use US centroid to avoid breaking distance filter.
            "lat": 39.8283,
            "lon": -98.5795,
            "format": "See source listing",
            "entryFee": "See source listing",
            "sections": [],
            "timeControl": "See source listing",
            "sourceId": source["id"],
            "sourceUrl": source["homepage"],
        })

    return out


# ----------------------------
# Parser: Michess events
# ----------------------------

MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

def _parse_michess_date_range(line: str) -> tuple[str, str] | None:
    """
    Attempts to parse lines like:
      'Fri, Feb 20 - Sun, Feb 22'
      'Sat, May 2 - Sat, May 2'
    Michess list views sometimes omit the year; we assume current year,
    and bump to next year if it looks far in the past.
    """
    # Normalize commas
    s = line.strip()

    # Pattern: "Fri, Feb 20 - Sun, Feb 22"
    m = re.search(
        r"\b[A-Za-z]{3},\s*([A-Za-z]{3,4})\s+(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3,4})\s+(\d{1,2})\b",
        s
    )
    if not m:
        return None

    mon1 = MONTHS_ABBR.get(m.group(1).lower())
    mon2 = MONTHS_ABBR.get(m.group(3).lower())
    if not mon1 or not mon2:
        return None

    d1 = int(m.group(2))
    d2 = int(m.group(4))

    today = date.today()
    y = today.year

    try:
        start = date(y, mon1, d1)
        end = date(y, mon2, d2)
    except ValueError:
        return None

    # If computed end is >30 days in the past, it probably means next year.
    if end < today and (today - end).days > 30:
        try:
            start = date(y + 1, mon1, d1)
            end = date(y + 1, mon2, d2)
        except ValueError:
            return None

    return (start.isoformat(), end.isoformat())


def parse_michess_events(listing_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Scrape michess.org/events by discovering event detail links,
    then scraping each detail page for title/date/location.
    """
    # Find detail links
    rel_links = sorted(set(re.findall(r'href="(/event-details/[^"]+)"', listing_html)))
    detail_urls = ["https://www.michess.org" + rel for rel in rel_links]

    out: list[dict[str, Any]] = []
    for url in detail_urls[:250]:  # safety cap
        try:
            detail_html = fetch_text(url)
        except Exception:
            continue

        lines = _strip_html_to_lines(detail_html)

        # Title: take first meaningful line near top
        title = None
        for ln in lines[:60]:
            low = ln.lower()
            if low in {"events", "event details", "michigan chess association"}:
                continue
            if len(ln) >= 6:
                title = ln
                break
        if not title:
            continue

        # Date range: find first parseable range line
        startDate = endDate = None
        for ln in lines[:200]:
            dr = _parse_michess_date_range(ln)
            if dr:
                startDate, endDate = dr
                break

        # If we can’t find dates, skip (don’t pollute feed)
        if not startDate:
            continue

        # Location: attempt to find "... , MI"
        city = "Unknown"
        venue = "See source listing"
        for ln in lines[:250]:
            mloc = re.search(r"\b([A-Za-z .'-]+),\s*MI\b", ln)
            if mloc:
                city = mloc.group(1).strip()
                venue = ln[:140]
                break

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate or startDate,
            "city": city,
            "state": "MI",
            "venue": venue,
            # Michigan centroid-ish; prevents distance filter from breaking.
            "lat": 44.3148,
            "lon": -85.6024,
            "format": "See source listing",
            "entryFee": "See source listing",
            "sections": [],
            "timeControl": "See source listing",
            "sourceId": source["id"],
            "sourceUrl": url,
        })

    return out


# ----------------------------
# Orchestrator
# ----------------------------

def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    parser = source["parser"]

    if parser == "uschess_upcoming":
        # US Chess may paginate. Try a reasonable number of pages.
        events: list[dict[str, Any]] = []
        for page in range(0, 40):
            url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
            html_text = fetch_text(url)
            page_events = parse_uschess_upcoming(html_text, source)
            if not page_events and page > 0:
                break
            events.extend(page_events)
        return events

    if parser == "michess_events":
        listing_html = fetch_text(source["endpoint"])
        return parse_michess_events(listing_html, source)

    return []


def main() -> None:
    all_events: list[dict[str, Any]] = []

    # Helpful output for Actions logs (so you can see what’s working)
    for source in SOURCE_CATALOG:
        try:
            events = fetch_source(source)
            print(f"[{source['id']}] fetched {len(events)} raw events")
            all_events.extend(events)
        except Exception as e:
            # Don’t hide failures during testing
            print(f"[{source['id']}] FAILED: {e}")

    # Filter out past events
    all_events = [e for e in all_events if is_upcoming(e)]

    # Dedupe
    all_events = dedupe(all_events)

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")


if __name__ == "__main__":
    main()
