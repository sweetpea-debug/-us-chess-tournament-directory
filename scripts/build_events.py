#!/usr/bin/env python3
"""
Build events.json for GitHub Pages.

Output: repo-root events.json
{
  "syncedAt": "<iso>",
  "events": [
    {
      "id": "...",
      "name": "...",
      "startDate": "YYYY-MM-DD",
      "endDate": "YYYY-MM-DD",
      "city": "...",
      "state": "MI",
      "sourceId": "uschess-upcoming" | "michess",
      "sourceUrl": "https://...",
      "sourceText": "multiline\\ntext..."
    }
  ]
}

Design goal (per request):
- Cards show only: name, date(s), city/state
- Details page shows: full sourceText + link to sourceUrl
- No lat/lon, no proximity filtering
- Standard library only
"""

from __future__ import annotations

import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


# ----------------------------
# Paths
# ----------------------------

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1] if HERE.parent.name == "scripts" else HERE.parent
OUTPUT_PATH = ROOT / "events.json"


# ----------------------------
# Config
# ----------------------------

DEFAULT_TIMEOUT_SECS = 25
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0; +https://github.com/)"

MAX_WORKERS = int(os.getenv("RADAR_WORKERS", "10"))
# For testing: RADAR_LIMIT=50 (limits total events per source)
LIMIT = int(os.getenv("RADAR_LIMIT", "0"))  # 0 = no limit
MAX_SOURCE_TEXT_CHARS = int(os.getenv("RADAR_MAX_TEXT", "28000"))

SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "uschess-upcoming",
        "name": "US Chess Upcoming Tournaments",
        "homepage": "https://new.uschess.org/upcoming-tournaments",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "homepage": "https://www.michess.org/events",
    },
]


US_STATE_ABBR = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO","connecticut":"CT",
    "delaware":"DE","florida":"FL","georgia":"GA","hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN",
    "iowa":"IA","kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA",
    "michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV",
    "new hampshire":"NH","new jersey":"NJ","new mexico":"NM","new york":"NY","north carolina":"NC","north dakota":"ND",
    "ohio":"OH","oklahoma":"OK","oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
    "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT","virginia":"VA","washington":"WA",
    "west virginia":"WV","wisconsin":"WI","wyoming":"WY","district of columbia":"DC",
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
}

MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}


# ----------------------------
# HTTP
# ----------------------------

def fetch_text(url: str) -> str:
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
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return raw.decode(errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"Fetch failed for {url}: {e}") from e


# ----------------------------
# HTML -> Lines / SourceText
# ----------------------------

BLOCK_END_TAGS = r"(p|div|li|h1|h2|h3|h4|h5|tr|td|th|section|article|header|footer|br)"

def _strip_html_to_lines(markup: str) -> list[str]:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    markup = re.sub(rf"</{BLOCK_END_TAGS}\s*>", "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines

def html_to_source_text(markup: str) -> str:
    """
    Turn a page into readable multiline text.
    This is intentionally simple, but preserves line breaks.
    """
    lines = _strip_html_to_lines(markup)

    # Remove some ultra-common nav noise
    noise = {
        "skip to main content", "donate", "login", "privacy policy", "terms of use",
        "facebook", "twitter", "youtube", "instagram", "rss",
    }
    cleaned: list[str] = []
    for ln in lines:
        low = ln.lower()
        if any(n in low for n in noise) and len(ln) < 80:
            continue
        cleaned.append(ln)

    text = "\n".join(cleaned).strip()
    if len(text) > MAX_SOURCE_TEXT_CHARS:
        text = text[:MAX_SOURCE_TEXT_CHARS].rstrip() + "\n\n[truncated]"
    return text


# ----------------------------
# Generic helpers
# ----------------------------

def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"

def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('name','')}|{e.get('startDate','')}|{e.get('city','')}|{e.get('state','')}|{e.get('sourceId','')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out

def is_upcoming(event: dict[str, Any]) -> bool:
    today = date.today().isoformat()
    end_date = str(event.get("endDate") or "")
    return bool(end_date) and end_date >= today


# ----------------------------
# Date parsing
# ----------------------------

def _parse_us_chess_date_one(s: str) -> date | None:
    s = s.strip()
    s = re.sub(r"^[A-Za-z]+,\s*", "", s)  # drop weekday
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$", s)
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None

def _parse_us_chess_date_range(s: str) -> tuple[str, str] | None:
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


def _parse_location_flexible(loc: str) -> tuple[str, str] | None:
    """
    Accept:
      - 'City, StateName'
      - 'City, ST'
      - 'City, ST, StateName'
    """
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) == 2:
        city, s2 = parts
        if re.fullmatch(r"[A-Z]{2}", s2):
            return city, s2
        abbr = US_STATE_ABBR.get(s2.lower())
        return (city, abbr) if abbr else None

    if len(parts) >= 3:
        city = parts[0]
        mid = parts[1]
        last = parts[-1]
        if re.fullmatch(r"[A-Z]{2}", mid):
            return city, mid
        abbr = US_STATE_ABBR.get(last.lower())
        return (city, abbr) if abbr else None

    return None


# ----------------------------
# US Chess: listing scrape
# ----------------------------

def _uschess_extract_cards(page_html: str, base_url: str) -> list[dict[str, Any]]:
    """
    Extract event URL + title from <h3><a href="...">TITLE</a></h3> then
    use text-lines around it to find location and date.
    """
    lines = _strip_html_to_lines(page_html)

    # map title->url from h3/a
    title_url: dict[str, str] = {}
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S,
    ):
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            title_url[title] = urljoin(base_url, href)

    out: list[dict[str, Any]] = []

    # heuristic: the page lines contain "### Title" blocks on many renders,
    # but to keep stable we just scan for title occurrences and look ahead.
    for i, ln in enumerate(lines):
        title = ln.strip()
        if title not in title_url:
            continue

        # look ahead for location/date
        loc = None
        dr = None
        for j in range(i + 1, min(i + 12, len(lines))):
            if loc is None:
                loc_try = _parse_location_flexible(lines[j])
                if loc_try:
                    loc = loc_try
                    continue
            if dr is None:
                dr_try = _parse_us_chess_date_range(lines[j])
                if dr_try:
                    dr = dr_try
                    continue

        if not loc or not dr:
            continue

        city, state = loc
        startDate, endDate = dr

        out.append(
            {
                "name": title,
                "startDate": startDate,
                "endDate": endDate,
                "city": city,
                "state": state,
                "sourceUrl": title_url[title],
            }
        )

    return out


def fetch_uschess_events() -> list[dict[str, Any]]:
    base = "https://new.uschess.org/upcoming-tournaments"
    events: list[dict[str, Any]] = []

    for page in range(0, 80):
        url = base if page == 0 else f"{base}?page={page}"
        html_text = fetch_text(url)
        page_events = _uschess_extract_cards(html_text, base)

        print(f"[uschess-upcoming] page={page} cards={len(page_events)}")

        if not page_events and page > 0:
            break

        events.extend(page_events)

        if LIMIT and len(events) >= LIMIT:
            events = events[:LIMIT]
            break

    # attach ids/sourceId
    finalized: list[dict[str, Any]] = []
    for e in events:
        finalized.append(
            {
                "id": f"uschess-upcoming-{sanitize_slug(e['name'])}-{e['startDate']}",
                "name": e["name"],
                "startDate": e["startDate"],
                "endDate": e["endDate"],
                "city": e["city"],
                "state": e["state"],
                "sourceId": "uschess-upcoming",
                "sourceUrl": e["sourceUrl"],
                "sourceText": "",  # filled in later
            }
        )
    return finalized


# ----------------------------
# Michess: listing -> detail URLs -> parse basics
# ----------------------------

def _michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    for path in re.findall(r'(/event-details/[a-z0-9\-]+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)

def _michess_title_from_html(detail_html: str) -> str:
    # Prefer <meta property="og:title">
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        t = html.unescape(m.group(1)).strip()
        if t:
            return t

    # Then <title>
    m = re.search(r"<title[^>]*>(.*?)</title>", detail_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        t = re.sub(r"\s*\|\s*Michigan Chess Association.*$", "", t, flags=re.I).strip()
        if t and len(t) > 4:
            return t

    # Then first <h1>
    m = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        if t and len(t) > 4:
            return t

    return ""

def _michess_parse_city_state(lines: list[str]) -> tuple[str, str]:
    # Look for "... City, ST ... United States"
    for ln in lines[:250]:
        if "United States" in ln and "," in ln:
            mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
            if mloc:
                return (mloc.group(1).strip(), mloc.group(2).strip())
    return ("Unknown", "MI")

def _michess_parse_date_range(lines: list[str], title: str) -> tuple[str, str] | None:
    # Try explicit "Feb 16, 2026" or "Fri, Feb 20 - Sun, Feb 22"
    for ln in lines[:160]:
        # Single: "Feb 16, 2026"
        m1 = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),\s*(20\d{2})\b", ln)
        if m1:
            mon = MONTHS.get(m1.group(1).lower())
            if mon:
                try:
                    d = date(int(m1.group(3)), mon, int(m1.group(2)))
                    return (d.isoformat(), d.isoformat())
                except ValueError:
                    pass

        # Range: "Fri, Feb 20 - Sun, Feb 22" (infer year)
        m2 = re.match(
            r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
            ln.strip()
        )
        if m2:
            mon1 = MONTHS_ABBR.get(m2.group(1).lower())
            mon2 = MONTHS_ABBR.get(m2.group(3).lower())
            if not mon1 or not mon2:
                continue
            year = date.today().year
            m_year = re.search(r"\b(20\d{2})\b", title)
            if m_year:
                year = int(m_year.group(1))
            try:
                start = date(year, mon1, int(m2.group(2)))
                end = date(year, mon2, int(m2.group(4)))
                if end < start:
                    end = start
                return (start.isoformat(), end.isoformat())
            except ValueError:
                pass

    return None

def fetch_michess_events() -> list[dict[str, Any]]:
    listing_url = "https://www.michess.org/events"
    listing_html = fetch_text(listing_url)
    urls = _michess_extract_detail_urls(listing_html, listing_url)

    print(f"[michess] found {len(urls)} event-details urls")

    if LIMIT:
        urls = urls[:LIMIT]

    events: list[dict[str, Any]] = []
    for u in urls:
        events.append(
            {
                "id": "",  # fill after parse
                "name": "",
                "startDate": "",
                "endDate": "",
                "city": "Unknown",
                "state": "MI",
                "sourceId": "michess",
                "sourceUrl": u,
                "sourceText": "",
            }
        )
    return events


# ----------------------------
# Enrichment: fetch each event page -> fill sourceText (+ Michess basics)
# ----------------------------

def enrich_event(event: dict[str, Any]) -> dict[str, Any]:
    url = event["sourceUrl"]
    html_text = fetch_text(url)
    source_text = html_to_source_text(html_text)
    event["sourceText"] = source_text

    if event["sourceId"] == "michess":
        title = _michess_title_from_html(html_text)
        if title:
            event["name"] = title

        lines = _strip_html_to_lines(html_text)
        dr = _michess_parse_date_range(lines, event.get("name") or "")
        if dr:
            event["startDate"], event["endDate"] = dr

        city, state = _michess_parse_city_state(lines)
        event["city"] = city
        event["state"] = state or "MI"

        if event["startDate"]:
            event["id"] = f"michess-{sanitize_slug(event['name'] or 'event')}-{event['startDate']}"

    return event


def enrich_events(events: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    if not events:
        return []

    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut_map = {ex.submit(enrich_event, e): e for e in events}
        done = 0
        total = len(fut_map)

        for fut in as_completed(fut_map):
            done += 1
            if done % 25 == 0 or done == total:
                print(f"[{label}] enriching {done}/{total} ...")

            try:
                ev = fut.result()
                out.append(ev)
            except Exception as e:
                base = fut_map[fut]
                print(f"[{label}] FAILED {base.get('sourceUrl')}: {e}")

    return out


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    all_events: list[dict[str, Any]] = []

    # US Chess
    us_events = fetch_uschess_events()
    print(f"[uschess-upcoming] fetched {len(us_events)} listing events")
    us_events = enrich_events(us_events, "uschess-upcoming")
    all_events.extend(us_events)

    # Michess (listing gives URLs; enrichment parses title/date/location)
    mi_events = fetch_michess_events()
    print(f"[michess] fetched {len(mi_events)} listing urls")
    mi_events = enrich_events(mi_events, "michess")
    all_events.extend(mi_events)

    # keep only events with a valid date
    all_events = [e for e in all_events if e.get("startDate") and e.get("endDate")]

    # upcoming only
    all_events = [e for e in all_events if is_upcoming(e)]

    # dedupe
    all_events = dedupe(all_events)

    # sort
    all_events.sort(key=lambda e: (e["startDate"], e.get("state", ""), e.get("name", "")))

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")


if __name__ == "__main__":
    main()
