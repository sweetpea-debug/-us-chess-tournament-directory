#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (Streamlined).

Goal:
- Cards show only: event name, date(s), city/state.
- Detail page shows: full source text (readable line breaks) + source link.

Sources (for now):
1) US Chess "Upcoming tournaments" + each event page
2) Michigan Chess Association event-details pages

Output:
repo-root events.json:
{
  "syncedAt": "<iso>",
  "events": [
    {
      "id": "...",
      "name": "...",
      "startDate": "YYYY-MM-DD",
      "endDate": "YYYY-MM-DD",
      "city": "...",
      "state": "XX",
      "sourceId": "...",
      "sourceUrl": "...",
      "fullText": "...\n...\n"
    }
  ]
}
"""

from __future__ import annotations

import html
import json
import re
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
# Sources
# ----------------------------
SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "uschess",
        "name": "US Chess",
        "endpoint": "https://new.uschess.org/upcoming-tournaments",
        "homepage": "https://new.uschess.org/upcoming-tournaments",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "endpoint": "https://www.michess.org/events",
        "homepage": "https://www.michess.org/events",
    },
]


# ----------------------------
# HTTP
# ----------------------------
DEFAULT_TIMEOUT_SECS = 30
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0; +https://github.com/)"

def fetch_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECS) as resp:
            raw = resp.read()
            return raw.decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"Fetch failed for {url}: {e}") from e


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
        key = f"{e.get('sourceId','')}|{e.get('name','')}|{e.get('startDate','')}|{e.get('city','')}|{e.get('state','')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out

def is_upcoming(event: dict[str, Any]) -> bool:
    # Keep events that end today or later
    today = date.today().isoformat()
    end_date = str(event.get("endDate") or "")
    return bool(end_date) and end_date >= today

def html_to_text_preserve_newlines(markup: str) -> str:
    """
    Convert HTML to readable plain text.
    Keeps line breaks by inserting newlines for block-level tags.
    """
    # Drop scripts/styles
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    # Replace common block tags with newlines
    block_end = r"</(p|div|li|ul|ol|h1|h2|h3|h4|h5|h6|tr|td|th|section|article|header|footer|br|hr)\s*>"
    markup = re.sub(block_end, "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)
    markup = re.sub(r"<hr\s*/?>", "\n", markup, flags=re.I)

    # Turn list items into "- " lines (helps readability)
    markup = re.sub(r"<li\b[^>]*>", "\n- ", markup, flags=re.I)

    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    # Normalize whitespace BUT keep newlines
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)

    # Collapse too many blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

def extract_city_state_from_text(text: str) -> tuple[str, str] | None:
    """
    Find first occurrence of 'City, ST' in text.
    """
    m = re.search(r"\b([A-Za-z][A-Za-z .'-]+),\s*([A-Z]{2})\b", text)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()

# ----------------------------
# US Chess parsing
# ----------------------------
def parse_us_chess_date_one(s: str) -> date | None:
    s = s.strip()
    s = re.sub(r"^[A-Za-z]+,\s*", "", s)  # remove weekday if present
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

def parse_us_chess_date_range(s: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in s.strip().split(" - ") if p.strip()]
    if not parts:
        return None
    start = parse_us_chess_date_one(parts[0])
    if not start:
        return None
    end = parse_us_chess_date_one(parts[1]) if len(parts) > 1 else start
    if not end:
        end = start
    return start.isoformat(), end.isoformat()

def parse_us_chess_listing_page(page_html: str, base_url: str) -> list[dict[str, Any]]:
    """
    Extract event links, titles, and try to capture location/date from nearby text.
    Then we'll enrich each by fetching its own page for fullText.
    """
    # Grab each card headline link:
    # <h3 ...><a href="/something">Title</a></h3>
    items: list[dict[str, Any]] = []
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h3>",
        page_html,
        flags=re.I | re.S,
    ):
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        url = urljoin(base_url, href)
        items.append({"name": title, "sourceUrl": url})
    return items

def enrich_us_chess_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """
    Fetch event page and extract:
    - fullText (formatted)
    - best-effort city/state
    - best-effort date range
    """
    url = ev["sourceUrl"]
    html_page = fetch_text(url)
    text = html_to_text_preserve_newlines(html_page)

    # Date range often appears as "Saturday, February ... - Sunday, ..."
    startDate = endDate = None
    for line in text.splitlines()[:200]:
        dr = parse_us_chess_date_range(line.strip())
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        # If we can't parse a date, skip (keeps junk out)
        return None

    loc = extract_city_state_from_text(text)
    city, state = ("Unknown", "US")
    if loc:
        city, state = loc

    return {
        "id": f"uschess-{sanitize_slug(ev['name'])}-{startDate}",
        "name": ev["name"],
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "sourceId": "uschess",
        "sourceUrl": url,
        "fullText": text,
    }

def fetch_us_chess_all() -> list[dict[str, Any]]:
    base = "https://new.uschess.org"
    listing_url = "https://new.uschess.org/upcoming-tournaments"

    all_listing_items: list[dict[str, Any]] = []
    # US Chess is paginated; keep going until a page returns 0 items.
    for page in range(0, 200):
        url = listing_url if page == 0 else f"{listing_url}?page={page}"
        html_page = fetch_text(url)
        items = parse_us_chess_listing_page(html_page, base)
        print(f"[uschess] page={page} listing_items={len(items)}")
        if not items and page > 0:
            break
        all_listing_items.extend(items)

    # Deduplicate by URL before enriching
    seen_urls: set[str] = set()
    unique = []
    for it in all_listing_items:
        if it["sourceUrl"] in seen_urls:
            continue
        seen_urls.add(it["sourceUrl"])
        unique.append(it)

    out: list[dict[str, Any]] = []
    total = len(unique)
    for idx, it in enumerate(unique, start=1):
        if idx % 20 == 0 or idx == 1 or idx == total:
            print(f"[uschess] enriching {idx}/{total} ...")
        try:
            ev = enrich_us_chess_event(it)
            if ev:
                out.append(ev)
        except Exception as e:
            print(f"[uschess] enrich FAILED {it['sourceUrl']}: {e}")

    return out


# ----------------------------
# Michess parsing
# ----------------------------
def michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    # href="/event-details/..."
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    # href="https://www.michess.org/event-details/..."
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    # JSON-ish fallback
    for path in re.findall(r'(/event-details/[a-z0-9\-]+-\d+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)

def michess_best_title(detail_html: str) -> str:
    # 1) og:title
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        t = html.unescape(m.group(1)).strip()
        if t and t.lower() not in {"michigan chess association"}:
            return t

    # 2) first <h1>
    m = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        if t and t.lower() not in {"michigan chess association"}:
            return t

    # 3) <title>
    m = re.search(r"<title[^>]*>(.*?)</title>", detail_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        # Strip site suffixes if present
        t = re.sub(r"\s*\|\s*Michigan Chess Association\s*$", "", t, flags=re.I).strip()
        return t or "Michigan Chess Event"

    return "Michigan Chess Event"

def infer_year_from_title(title: str) -> int:
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    today = date.today()
    return today.year

def parse_michess_date_range(line: str, title: str) -> tuple[str, str] | None:
    """
    Michess tends to show: 'Mon, Feb 16 - Tue, Feb 17' (no year)
    We'll infer year from title or fallback to current year.
    """
    s = line.strip()

    m = re.match(
        r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
        s,
    )
    if not m:
        # single-day pattern: 'Mon, Feb 16'
        m2 = re.match(r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$", s)
        if not m2:
            return None
        mon = MONTHS_ABBR.get(m2.group(1).lower())
        if not mon:
            return None
        y = infer_year_from_title(title)
        d = int(m2.group(2))
        try:
            dt = date(y, mon, d)
            return dt.isoformat(), dt.isoformat()
        except ValueError:
            return None

    mon1 = MONTHS_ABBR.get(m.group(1).lower())
    mon2 = MONTHS_ABBR.get(m.group(3).lower())
    if not mon1 or not mon2:
        return None
    d1 = int(m.group(2))
    d2 = int(m.group(4))
    y = infer_year_from_title(title)

    try:
        start = date(y, mon1, d1)
        end = date(y, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

def parse_michess_detail(url: str) -> dict[str, Any] | None:
    html_page = fetch_text(url)
    title = michess_best_title(html_page)
    text = html_to_text_preserve_newlines(html_page)

    # Dates: scan early lines
    startDate = endDate = None
    for ln in text.splitlines()[:150]:
        dr = parse_michess_date_range(ln.strip(), title)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    # Location: find City, ST (MI typically) anywhere in text
    loc = extract_city_state_from_text(text)
    city, state = ("Unknown", "MI")
    if loc:
        city, state = loc

    return {
        "id": f"michess-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "sourceId": "michess",
        "sourceUrl": url,
        "fullText": text,
    }

def fetch_michess_all() -> list[dict[str, Any]]:
    listing_url = "https://www.michess.org/events"
    listing_html = fetch_text(listing_url)
    urls = michess_extract_detail_urls(listing_html, "https://www.michess.org")
    print(f"[michess] found {len(urls)} event-details urls")

    out: list[dict[str, Any]] = []
    for idx, u in enumerate(urls, start=1):
        if idx % 10 == 0 or idx == 1 or idx == len(urls):
            print(f"[michess] fetching {idx}/{len(urls)} ...")
        try:
            ev = parse_michess_detail(u)
            if ev:
                out.append(ev)
        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

    return out


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    all_events: list[dict[str, Any]] = []

    # US Chess
    try:
        us_events = fetch_us_chess_all()
        print(f"[uschess] fetched {len(us_events)} raw events")
        all_events.extend(us_events)
    except Exception as e:
        print(f"[uschess] FAILED: {e}")

    # Michess
    try:
        mi_events = fetch_michess_all()
        print(f"[michess] fetched {len(mi_events)} raw events")
        all_events.extend(mi_events)
    except Exception as e:
        print(f"[michess] FAILED: {e}")

    # Filter upcoming + dedupe
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
