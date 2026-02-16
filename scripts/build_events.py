#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (streamlined mode).

Goal:
- Cards: name, date(s), city/state
- Detail page: full source text + link
- Filter: state only
- No geo / no venue parsing required

Sources:
1) US Chess "Upcoming Tournaments" listing (new.uschess.org)
2) Michigan Chess Association event detail pages (michess.org)

Output:
- repo-root events.json:
  { "syncedAt": "<iso>", "events": [ {id,name,startDate,endDate,city,state,sourceId,sourceUrl,sourceText}, ... ] }

Notes:
- Standard library only (works in GitHub Actions without pip installs).
- We store readable line breaks in sourceText (not one giant paragraph).
"""

from __future__ import annotations

import html as html_lib
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
ROOT = HERE.parents[1] if HERE.parent.name == "scripts" else HERE.parent
OUTPUT_PATH = ROOT / "events.json"


# ----------------------------
# Sources
# ----------------------------

SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "uschess-upcoming",
        "name": "US Chess Upcoming Tournaments",
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

DEFAULT_TIMEOUT_SECS = 25
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0; +https://github.com/)"

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
# HTML -> readable text lines
# ----------------------------

_BLOCK_END_TAGS = (
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4",
    "section", "article", "header", "footer", "tr", "td", "th",
)

def html_to_text_lines(markup: str) -> list[str]:
    """
    Convert HTML into readable text lines with line breaks preserved.
    This is intentionally lightweight (no BeautifulSoup).
    """
    # Drop scripts/styles
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    # Turn <br> into newline
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    # Add newlines after common block closing tags
    for t in _BLOCK_END_TAGS:
        markup = re.sub(rf"</{t}\s*>", "\n", markup, flags=re.I)

    # Bulletize list items a bit
    markup = re.sub(r"<li\b[^>]*>", "\n- ", markup, flags=re.I)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html_lib.unescape(text)

    # Normalize whitespace but KEEP blank lines meaningfully
    raw_lines = text.splitlines()
    lines: list[str] = []
    blank_run = 0

    for raw in raw_lines:
        line = re.sub(r"[ \t\r\f\v]+", " ", raw).strip()

        if not line:
            blank_run += 1
            # allow at most one blank line in a row
            if blank_run <= 1 and lines:
                lines.append("")
            continue

        blank_run = 0
        lines.append(line)

    # Trim trailing blanks
    while lines and lines[-1] == "":
        lines.pop()

    return lines


def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"


def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Dedupe by (name + startDate + city + state). Prefer first occurrence.
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
# Date parsing helpers
# ----------------------------

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
}
MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

def parse_us_date_one(s: str) -> Optional[date]:
    """
    Parses:
      'Wednesday, February 18, 2026'
      'February 18, 2026'
    """
    s = s.strip()
    s = re.sub(r"^[A-Za-z]+,\s*", "", s)  # drop weekday if present
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


def parse_us_date_range(s: str) -> Optional[tuple[str, str]]:
    """
    Parses:
      'Saturday, January 3, 2026 - Sunday, January 4, 2026'
    or:
      'Wednesday, February 18, 2026'
    """
    s = s.strip()
    parts = [p.strip() for p in s.split(" - ")]
    if not parts:
        return None
    start = parse_us_date_one(parts[0])
    if not start:
        return None
    end = parse_us_date_one(parts[1]) if len(parts) > 1 else start
    if not end:
        end = start
    return start.isoformat(), end.isoformat()


# ----------------------------
# Location parsing helper
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

def parse_city_state(loc: str) -> Optional[tuple[str, str]]:
    """
    Accept:
      - 'City, ST'
      - 'City, StateName'
      - 'City, ST, StateName'  (sometimes seen)
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
# US Chess: listing + detail
# ----------------------------

def uschess_extract_title_url_map(page_html: str, base_url: str) -> dict[str, str]:
    """
    Map normalized title -> full event URL.
    """
    m: dict[str, str] = {}
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S
    ):
        title = html_lib.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        m[title.lower()] = urljoin(base_url, href)
    return m


def uschess_parse_listing_page(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract events from listing page text. We only need:
      - title
      - city/state
      - date range
      - url (from title->url map)
    """
    lines = html_to_text_lines(page_html)
    title_url = uschess_extract_title_url_map(page_html, source["homepage"])

    out: list[dict[str, Any]] = []

    # Heuristic: listing text tends to look like blocks:
    #   <title>
    #   <city, state...>
    #   <date range>
    #
    # We scan for a line that matches a known title (via the url map),
    # then look forward for location/date.
    url_title_set = set(title_url.keys())

    for i in range(len(lines)):
        title = lines[i].strip()
        if title.lower() not in url_title_set:
            continue

        loc = None
        dr = None

        for j in range(i + 1, min(i + 8, len(lines))):
            if loc is None:
                loc_try = parse_city_state(lines[j])
                if loc_try:
                    loc = loc_try
                    continue
            if dr is None:
                dr_try = parse_us_date_range(lines[j])
                if dr_try:
                    dr = dr_try
                    continue

        if not loc or not dr:
            continue

        city, state = loc
        startDate, endDate = dr
        event_url = title_url.get(title.lower(), source["homepage"])

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": state,
            "sourceId": source["id"],
            "sourceUrl": event_url,
            # sourceText filled in later (enrichment pass)
        })

    return out


def uschess_enrich_with_source_text(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Fetch each event page and attach readable `sourceText`.
    This is the expensive part.
    """
    enriched: list[dict[str, Any]] = []
    total = len(events)

    for idx, ev in enumerate(events, start=1):
        url = ev.get("sourceUrl") or ""
        try:
            detail_html = fetch_text(url)
            detail_lines = html_to_text_lines(detail_html)

            # Keep it readable + not insane in file size
            text = "\n".join(detail_lines)
            text = text.strip()

            # Hard cap (keeps events.json from exploding)
            if len(text) > 20000:
                text = text[:20000].rstrip() + "\n\n[Truncated]"

            ev2 = dict(ev)
            ev2["sourceText"] = text
            enriched.append(ev2)

        except Exception as e:
            # Keep event, but record failure text so detail page isn't empty.
            ev2 = dict(ev)
            ev2["sourceText"] = f"[Could not fetch source text]\n{e}"
            enriched.append(ev2)

        # light throttling (polite)
        if idx % 25 == 0:
            print(f"[uschess-upcoming] enriched {idx}/{total} ...")
        time.sleep(0.05)

    return enriched


def fetch_uschess(source: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Paginate US Chess listing pages, then enrich each event with its detail page text.
    """
    all_events: list[dict[str, Any]] = []

    # The site *may* paginate with ?page=1,2,... or sometimes different behavior.
    # We’ll try a bunch and stop after 2 consecutive empty pages.
    empty_run = 0

    for page in range(0, 80):
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        page_html = fetch_text(url)
        page_events = uschess_parse_listing_page(page_html, source)

        print(f"[uschess-upcoming] page={page} parsed={len(page_events)}")

        if not page_events:
            empty_run += 1
            if empty_run >= 2 and page > 0:
                break
        else:
            empty_run = 0

        all_events.extend(page_events)

    # Dedupe before enrichment (saves fetches)
    all_events = dedupe(all_events)

    # Enrich with full source text (this is what you want on the detail page)
    print(f"[uschess-upcoming] enriching {len(all_events)} events with source text…")
    all_events = uschess_enrich_with_source_text(all_events)

    return all_events


# ----------------------------
# Michess: extract detail urls + parse
# ----------------------------

def michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    # href="/event-details/..."
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    # absolute
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    # JSON-ish fallback
    for path in re.findall(r'(/event-details/[a-z0-9\-]+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)


def michess_pick_title(detail_html: str, text_lines: list[str]) -> str:
    """
    Title extraction that avoids returning 'Michigan Chess Association'.
    Prefer:
      - og:title
      - first <h1> text
      - first meaningful line not in site nav
    """
    # og:title
    m = re.search(r'property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        t = html_lib.unescape(m.group(1)).strip()
        if t and t.lower() != "michigan chess association":
            return t

    # <h1>...</h1>
    m2 = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
    if m2:
        t = html_lib.unescape(re.sub(r"<[^>]+>", " ", m2.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        if t and t.lower() != "michigan chess association":
            return t

    # fallback: first meaningful line that's not global nav-y
    bad = {
        "michigan chess association", "events", "event", "submit event",
        "donate", "home", "results", "membership", "news",
    }
    for ln in text_lines[:120]:
        s = ln.strip()
        if len(s) < 6:
            continue
        low = s.lower()
        if low in bad:
            continue
        # avoid returning long menu blobs
        if "skip to" in low or "privacy policy" in low:
            continue
        if low.startswith("michigan chess association"):
            continue
        return s

    return "Michigan event"


def michess_parse_date_range(text_lines: list[str], title: str) -> Optional[tuple[str, str]]:
    """
    Michess pages commonly have:
      'Fri, Feb 16 - Fri, Feb 16'  (no year)
      or sometimes include a year.
    We infer year using:
      - year in title
      - else current year / next year if date already passed
    """
    year = None
    my = re.search(r"\b(20\d{2})\b", title)
    if my:
        year = int(my.group(1))
    else:
        year = date.today().year

    def build_date(mon: int, day: int) -> date:
        d0 = date(year, mon, day)
        # if inferred year produces a date far in the past, bump to next year
        if d0 < date.today().replace(month=1, day=1) and (date.today() - d0).days > 120:
            return date(year + 1, mon, day)
        return d0

    # pattern: "Fri, Feb 16 - Tue, Feb 17"
    for ln in text_lines[:200]:
        s = ln.strip()
        m = re.match(
            r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})",
            s
        )
        if m:
            mon1 = MONTHS_ABBR.get(m.group(1).lower())
            mon2 = MONTHS_ABBR.get(m.group(3).lower())
            if not mon1 or not mon2:
                continue
            d1 = int(m.group(2)); d2 = int(m.group(4))
            try:
                start = build_date(mon1, d1)
                end = build_date(mon2, d2)
                if end < start:
                    end = start
                return start.isoformat(), end.isoformat()
            except ValueError:
                continue

    # fallback: any "Month Day, Year" in first section
    for ln in text_lines[:220]:
        dr = parse_us_date_range(ln)
        if dr:
            return dr

    return None


def michess_parse_city_state(text_lines: list[str]) -> tuple[str, str]:
    """
    Find first 'City, ST' pattern in the page text.
    """
    for ln in text_lines[:300]:
        m = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return "Unknown", "MI"


def fetch_michess(source: dict[str, Any]) -> list[dict[str, Any]]:
    listing_html = fetch_text(source["endpoint"])
    urls = michess_extract_detail_urls(listing_html, source["homepage"])

    print(f"[michess] found {len(urls)} event-details urls")
    events: list[dict[str, Any]] = []

    for idx, u in enumerate(urls, start=1):
        try:
            detail_html = fetch_text(u)
            lines = html_to_text_lines(detail_html)

            title = michess_pick_title(detail_html, lines)
            dr = michess_parse_date_range(lines, title)
            if not dr:
                continue
            startDate, endDate = dr

            city, state = michess_parse_city_state(lines)

            text = "\n".join(lines).strip()
            if len(text) > 20000:
                text = text[:20000].rstrip() + "\n\n[Truncated]"

            events.append({
                "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
                "name": title,
                "startDate": startDate,
                "endDate": endDate,
                "city": city,
                "state": state,
                "sourceId": source["id"],
                "sourceUrl": u,
                "sourceText": text,
            })

        except Exception as e:
            print(f"[michess] FAILED {u}: {e}")

        if idx % 20 == 0:
            print(f"[michess] processed {idx}/{len(urls)} …")
        time.sleep(0.05)

    return events


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    us_source = next(s for s in SOURCE_CATALOG if s["id"] == "uschess-upcoming")
    mi_source = next(s for s in SOURCE_CATALOG if s["id"] == "michess")

    all_events: list[dict[str, Any]] = []

    try:
        us_events = fetch_uschess(us_source)
        print(f"[uschess-upcoming] fetched {len(us_events)} total (with source text)")
        all_events.extend(us_events)
    except Exception as e:
        print(f"[uschess-upcoming] FAILED: {e}")

    try:
        mi_events = fetch_michess(mi_source)
        print(f"[michess] fetched {len(mi_events)} total (with source text)")
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
