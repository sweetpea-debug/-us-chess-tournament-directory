#!/usr/bin/env python3
"""
FAST MODE ingest for chess tournaments.

Goal (per user request):
- Cards show ONLY: name, date(s), city/state.
- Detail page shows:
  - all readable text from the source event page (fullText)
  - link to the official sourceUrl

Sources (test mode):
1) US Chess Upcoming Tournaments listing (new.uschess.org/upcoming-tournaments)
   - paginate list pages
   - store each event card + sourceUrl
   - fetch event detail page ONLY to capture fullText (no parsing of fee/tc/etc)

2) Michigan Chess Association events (michess.org/events)
   - extract /event-details/... URLs
   - fetch each detail page, capture title/dates/location (best effort) + fullText

Output:
  repo-root events.json:
    { "syncedAt": "<iso>", "events": [ ... ] }

Standard library only.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


# ----------------------------
# Paths
# ----------------------------
HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]  # scripts/build_events.py -> repo root
OUTPUT_PATH = ROOT / "events.json"


# ----------------------------
# Config
# ----------------------------
DEFAULT_TIMEOUT_SECS = 30
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0; +https://github.com/)"
MAX_TEXT_CHARS = 20000  # keep detail payloads reasonable
SLEEP_SECS = 0.05       # light pacing


# ----------------------------
# Sources
# ----------------------------
SOURCES: list[dict[str, str]] = [
    {
        "id": "uschess",
        "name": "US Chess",
        "listing": "https://new.uschess.org/upcoming-tournaments",
        "homepage": "https://new.uschess.org/upcoming-tournaments",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "listing": "https://www.michess.org/events",
        "homepage": "https://www.michess.org/events",
    },
]


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
            return raw.decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"Fetch failed for {url}: {e}") from e


# ----------------------------
# HTML -> text
# ----------------------------
def strip_html_to_text(markup: str) -> str:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def strip_html_to_lines(markup: str) -> list[str]:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"</(p|div|li|h1|h2|h3|h4|tr|td|th|section|article|header|footer)\s*>", "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


# ----------------------------
# Helpers
# ----------------------------
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

def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"

def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
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
    today = date.today().isoformat()
    end_date = str(event.get("endDate") or "")
    return bool(end_date) and end_date >= today

def parse_location_flexible(loc: str) -> Optional[tuple[str, str]]:
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

def parse_us_chess_date_one(s: str) -> Optional[date]:
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

def parse_us_chess_date_range(s: str) -> Optional[tuple[str, str]]:
    s = s.strip()
    parts = [p.strip() for p in s.split(" - ")]
    if not parts:
        return None
    start = parse_us_chess_date_one(parts[0])
    if not start:
        return None
    end = parse_us_chess_date_one(parts[1]) if len(parts) > 1 else start
    if not end:
        end = start
    return start.isoformat(), end.isoformat()

def infer_year(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    return date.today().year

def parse_michess_date_line(line: str, year: int) -> Optional[tuple[str, str]]:
    s = line.strip()
    # "Fri, Feb 20 - Sun, Feb 22"
    m = re.match(
        r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
        s,
    )
    if not m:
        return None
    mon1 = MONTHS_ABBR.get(m.group(1).lower())
    mon2 = MONTHS_ABBR.get(m.group(3).lower())
    if not mon1 or not mon2:
        return None
    d1, d2 = int(m.group(2)), int(m.group(4))
    try:
        start = date(year, mon1, d1)
        end = date(year, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None


# ----------------------------
# US Chess
# ----------------------------
def uschess_listing_items(listing_html: str, base_url: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h3>",
        listing_html,
        flags=re.I | re.S,
    ):
        title = strip_html_to_text(inner)
        if not title:
            continue
        url = urljoin(base_url, href)

        # local snippet for location/date
        idx = listing_html.lower().find(href.lower())
        snippet = listing_html[max(0, idx - 1200): idx + 2000]
        lines = strip_html_to_lines(snippet)

        location_line = ""
        date_line = ""
        start_i = 0
        for i, ln in enumerate(lines):
            if ln.strip().lower() == title.strip().lower():
                start_i = i
                break

        for ln in lines[start_i:start_i + 12]:
            if not location_line and parse_location_flexible(ln):
                location_line = ln
            if not date_line and parse_us_chess_date_range(ln):
                date_line = ln

        items.append(
            {
                "title": title.strip(),
                "url": url,
                "location_line": location_line,
                "date_line": date_line,
            }
        )
    return items

def fetch_uschess() -> list[dict[str, Any]]:
    src = next(s for s in SOURCES if s["id"] == "uschess")
    base = src["homepage"]

    seen_urls: set[str] = set()
    listing: list[dict[str, str]] = []

    for page in range(0, 80):
        url = src["listing"] if page == 0 else f"{src['listing']}?page={page}"
        html_text = fetch_text(url)
        items = uschess_listing_items(html_text, base)
        new_items = [it for it in items if it["url"] not in seen_urls]
        for it in new_items:
            seen_urls.add(it["url"])
        print(f"[uschess] page={page} items={len(items)} new={len(new_items)}")

        if page > 0 and len(new_items) == 0:
            break

        listing.extend(new_items)
        time.sleep(SLEEP_SECS)

    events: list[dict[str, Any]] = []
    for i, it in enumerate(listing, start=1):
        title = it["title"]
        loc = parse_location_flexible(it.get("location_line", "") or "")
        dr = parse_us_chess_date_range(it.get("date_line", "") or "")

        city, state = ("Unknown", "US")
        if loc:
            city, state = loc

        startDate, endDate = ("", "")
        if dr:
            startDate, endDate = dr

        # Fetch detail for fullText
        try:
            detail_html = fetch_text(it["url"])
            full_text = strip_html_to_text(detail_html)[:MAX_TEXT_CHARS]
        except Exception as e:
            print(f"[uschess] detail FAILED {it['url']}: {e}")
            full_text = ""

        # If listing didn't yield a date, try first 200 lines on detail page
        if not startDate and full_text:
            lines = strip_html_to_lines(detail_html)
            for ln in lines[:220]:
                dr2 = parse_us_chess_date_range(ln)
                if dr2:
                    startDate, endDate = dr2
                    break

        if not startDate:
            continue

        events.append(
            {
                "id": f"uschess-{sanitize_slug(title)}-{startDate}",
                "name": title,
                "startDate": startDate,
                "endDate": endDate or startDate,
                "city": city,
                "state": state,
                "sourceId": "uschess",
                "sourceUrl": it["url"],
                "fullText": full_text,
            }
        )

        if i % 25 == 0:
            print(f"[uschess] enriched {i}/{len(listing)}")

        time.sleep(SLEEP_SECS)

    return events


# ----------------------------
# Michess
# ----------------------------
def michess_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)
    for path in re.findall(r'(/event-details/[a-z0-9\-]+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))
    return sorted(urls)

def michess_title(detail_html: str) -> str:
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        return html.unescape(m.group(1)).strip()
    m2 = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
    if m2:
        return strip_html_to_text(m2.group(1)).strip()
    return ""

def fetch_michess() -> list[dict[str, Any]]:
    src = next(s for s in SOURCES if s["id"] == "michess")
    listing_html = fetch_text(src["listing"])
    urls = michess_detail_urls(listing_html, src["homepage"])
    print(f"[michess] found {len(urls)} event-details urls")

    events: list[dict[str, Any]] = []
    for i, u in enumerate(urls, start=1):
        try:
            detail_html = fetch_text(u)
            full_text = strip_html_to_text(detail_html)[:MAX_TEXT_CHARS]
            title = michess_title(detail_html)

            # best-effort date + location from visible lines
            lines = strip_html_to_lines(detail_html)
            yr = infer_year(title + " " + " ".join(lines[:80]))

            startDate = endDate = ""
            for ln in lines[:200]:
                dr = parse_michess_date_line(ln, yr)
                if dr:
                    startDate, endDate = dr
                    break

            city, state = ("Unknown", "MI")
            for ln in lines[:350]:
                mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
                if mloc:
                    city = mloc.group(1).strip()
                    state = mloc.group(2).strip()
                    break

            if not title or not startDate:
                continue

            events.append(
                {
                    "id": f"michess-{sanitize_slug(title)}-{startDate}",
                    "name": title,
                    "startDate": startDate,
                    "endDate": endDate or startDate,
                    "city": city,
                    "state": state,
                    "sourceId": "michess",
                    "sourceUrl": u,
                    "fullText": full_text,
                }
            )
        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

        if i % 15 == 0:
            print(f"[michess] fetched {i}/{len(urls)}")

        time.sleep(SLEEP_SECS)

    return events


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    all_events: list[dict[str, Any]] = []

    try:
        us = fetch_uschess()
        print(f"[uschess] fetched {len(us)} events")
        all_events.extend(us)
    except Exception as e:
        print(f"[uschess] FAILED: {e}")

    try:
        mi = fetch_michess()
        print(f"[michess] fetched {len(mi)} events")
        all_events.extend(mi)
    except Exception as e:
        print(f"[michess] FAILED: {e}")

    all_events = [e for e in all_events if is_upcoming(e)]
    all_events = dedupe(all_events)

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")


if __name__ == "__main__":
    main()
