#!/usr/bin/env python3
"""
Build events.json for the static site.

Goal:
- Cards show only: event name, date(s), city, state
- Detail page shows: full extracted source text + link to source
- State-only filtering (no distance feature, no refresh button)

Sources (test set):
- US Chess Upcoming Tournaments (new.uschess.org/upcoming-tournaments)
- Michigan Chess Association events (michess.org/events -> /event-details/... pages)

Output:
  events.json (repo root)
  { "syncedAt": "<iso>", "events": [ ... ] }

Notes:
- Standard library only.
- To keep GitHub Actions runtime reasonable, we only keep events starting within
  the next DAYS_AHEAD days (default 365). Increase if you truly want more.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


# ----------------------------
# Config
# ----------------------------

DEFAULT_TIMEOUT_SECS = 25
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0; +https://github.com/)"
DAYS_AHEAD = 365  # <-- change this if you want a smaller/bigger window


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
        "type": "uschess_upcoming",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "endpoint": "https://www.michess.org/events",
        "homepage": "https://www.michess.org/events",
        "type": "michess_events",
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
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return raw.decode(errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"Fetch failed for {url}: {e}") from e


# ----------------------------
# HTML -> lines helpers
# ----------------------------

_BLOCK_END_TAGS = (
    "p", "div", "li", "h1", "h2", "h3", "h4",
    "tr", "td", "th", "section", "article", "header", "footer",
    "main", "nav", "aside", "br"
)

def strip_to_lines(markup: str) -> list[str]:
    """Convert HTML into cleaned text lines, keeping a reasonable amount of line breaks."""
    # Drop scripts/styles
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    # Turn block ends into newlines
    markup = re.sub(
        r"</(" + "|".join(_BLOCK_END_TAGS) + r")\s*>",
        "\n",
        markup,
        flags=re.I,
    )
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    # Remove remaining tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    # Normalize into non-empty lines
    out: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def extract_main_html(markup: str) -> str:
    """
    Try to pull the main content region to avoid dumping the entire site's navigation.
    Best-effort: <main>...</main>, else <article>...</article>, else original.
    """
    m = re.search(r"<main\b[^>]*>(.*?)</main>", markup, flags=re.I | re.S)
    if m:
        return m.group(1)
    m = re.search(r"<article\b[^>]*>(.*?)</article>", markup, flags=re.I | re.S)
    if m:
        return m.group(1)
    return markup


def normalize_whitespace_text(lines: list[str], max_chars: int = 12000) -> str:
    """
    Join lines into a multi-line block, trimmed to max_chars.
    """
    text = "\n".join(lines)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[truncated]"
    return text


def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:90] if value else "event"


def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Dedupe by (sourceId + sourceUrl) primarily; fallback to (name+startDate+city+state).
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('sourceId','')}|{e.get('sourceUrl','')}"
        if key == "|":
            key = f"{e.get('name','')}|{e.get('startDate','')}|{e.get('city','')}|{e.get('state','')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# ----------------------------
# Date helpers
# ----------------------------

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
}

def parse_us_date_one(s: str) -> date | None:
    """
    US Chess often uses:
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


def parse_us_date_range(s: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in s.strip().split(" - ")]
    if not parts:
        return None
    start = parse_us_date_one(parts[0])
    if not start:
        return None
    end = parse_us_date_one(parts[1]) if len(parts) > 1 else start
    if not end:
        end = start
    return start.isoformat(), end.isoformat()


def within_window(start_iso: str) -> bool:
    try:
        start_d = date.fromisoformat(start_iso)
    except Exception:
        return False
    today = date.today()
    return today <= start_d <= (today + timedelta(days=DAYS_AHEAD))


def is_upcoming(end_iso: str) -> bool:
    try:
        end_d = date.fromisoformat(end_iso)
    except Exception:
        return False
    return end_d >= date.today()


# ----------------------------
# Location parsing
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

def parse_city_state(loc: str) -> tuple[str, str] | None:
    """
    Accept:
      - 'City, ST'
      - 'City, StateName'
      - 'City, ST, StateName' (sometimes appears)
    """
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) < 2:
        return None

    city = parts[0]
    st_candidate = parts[1]

    if re.fullmatch(r"[A-Z]{2}", st_candidate):
        return city, st_candidate

    abbr = US_STATE_ABBR.get(st_candidate.lower())
    if abbr:
        return city, abbr

    # sometimes last part is full state name
    abbr = US_STATE_ABBR.get(parts[-1].lower())
    if abbr:
        return city, abbr

    return None


# ----------------------------
# US Chess: list page parsing
# ----------------------------

def uschess_extract_cards(page_html: str, base_url: str) -> list[dict[str, str]]:
    """
    Pull title + href from h3/a blocks (best-effort),
    then we'll use text-line scanning to find location/date near that title.
    """
    cards: list[dict[str, str]] = []
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h3>",
        page_html,
        flags=re.I | re.S,
    ):
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        cards.append({"title": title, "url": urljoin(base_url, href)})
    return cards


def uschess_parse_list_page(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Parse the list page to get title, url, date range, city/state.
    Then later we'll fetch each detail page to store sourceText.
    """
    lines = strip_to_lines(extract_main_html(page_html))
    cards = uschess_extract_cards(page_html, source["homepage"])

    # Build a quick index for locating nearby lines by title
    # (We match on the title text found in lines.)
    out: list[dict[str, Any]] = []

    for c in cards:
        title = c["title"]
        url = c["url"]

        # Find title line index
        idx = -1
        title_low = title.lower()
        for i, ln in enumerate(lines):
            if ln.lower().strip() == title_low:
                idx = i
                break
        if idx < 0:
            # sometimes title in text differs slightly; skip rather than inventing junk
            continue

        loc = None
        dr = None

        # Look ahead a bit for location + date
        for j in range(idx + 1, min(idx + 12, len(lines))):
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

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": state,
            "sourceId": source["id"],
            "sourceUrl": url,
            "sourceText": "",  # filled later by enrichment
        })

    return out


def uschess_enrich_source_text(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    For each event within our date window, fetch its detail page and extract main text.
    This is the slow part. We keep it bounded by DAYS_AHEAD.
    """
    enriched: list[dict[str, Any]] = []
    total = len(events)
    for idx, ev in enumerate(events, start=1):
        if not within_window(ev["startDate"]) or not is_upcoming(ev["endDate"]):
            continue

        try:
            detail_html = fetch_text(ev["sourceUrl"])
            main_html = extract_main_html(detail_html)
            lines = strip_to_lines(main_html)

            # If main extraction is too short (bad match), fall back to whole doc
            if len(lines) < 40:
                lines = strip_to_lines(detail_html)

            ev["sourceText"] = normalize_whitespace_text(lines, max_chars=14000)
        except Exception as e:
            ev["sourceText"] = f"(Could not fetch source text: {e})"

        if idx % 25 == 0 or idx == total:
            print(f"[uschess] enriched {idx}/{total} ...")

        enriched.append(ev)

    return enriched


def fetch_uschess(source: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for page in range(0, 80):  # adjust if they truly have more
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        page_html = fetch_text(url)
        page_events = uschess_parse_list_page(page_html, source)
        print(f"[uschess] page={page} parsed={len(page_events)}")
        if not page_events and page > 0:
            break
        events.extend(page_events)

    print(f"[uschess] fetched {len(events)} list events (pre-enrich)")
    # Enrich only the events in our window (to keep runtime sane)
    return uschess_enrich_source_text(events)


# ----------------------------
# Michess: listing -> event-details pages
# ----------------------------

def michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    # href="/event-details/..."
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    # absolute
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    # fallback in JSON
    for path in re.findall(r'(/event-details/[a-z0-9\-]+-\d+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)


def michess_extract_title(detail_html: str) -> str:
    # Prefer <h1>
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            return t

    # Next: og:title
    m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        t = html.unescape(m.group(1)).strip()
        if t:
            return t

    # Finally: <title>
    m = re.search(r"<title\b[^>]*>(.*?)</title>", detail_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            return t

    return ""


MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

def michess_infer_year(detail_html: str) -> int:
    # Look for a 4-digit year anywhere
    m = re.search(r"\b(20\d{2})\b", detail_html)
    if m:
        return int(m.group(1))
    return date.today().year


def michess_parse_date_range(lines: list[str], year: int) -> tuple[str, str] | None:
    """
    Michess often shows:
      'Fri, Feb 20 - Sun, Feb 22'
    """
    for ln in lines[:180]:
        s = ln.strip()
        m = re.match(
            r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
            s
        )
        if not m:
            continue
        mon1 = MONTHS_ABBR.get(m.group(1).lower())
        mon2 = MONTHS_ABBR.get(m.group(3).lower())
        if not mon1 or not mon2:
            continue
        d1 = int(m.group(2))
        d2 = int(m.group(4))
        try:
            start = date(year, mon1, d1)
            end = date(year, mon2, d2)
            if end < start:
                end = start
            return start.isoformat(), end.isoformat()
        except ValueError:
            continue
    return None


def michess_parse_city_state(lines: list[str]) -> tuple[str, str] | None:
    # Look for a "City, ST" near the top-ish; michess pages often include "United States"
    for ln in lines[:260]:
        if "United States" in ln and "," in ln:
            m = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
            if m:
                return m.group(1).strip(), m.group(2).strip()
    # fallback: any City, ST
    for ln in lines[:260]:
        m = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None


def fetch_michess(source: dict[str, Any]) -> list[dict[str, Any]]:
    listing_html = fetch_text(source["endpoint"])
    urls = michess_extract_detail_urls(listing_html, source["homepage"])
    print(f"[michess] found {len(urls)} event-details urls")

    out: list[dict[str, Any]] = []
    for idx, u in enumerate(urls, start=1):
        try:
            detail_html = fetch_text(u)
            title = michess_extract_title(detail_html)
            if not title:
                continue

            year = michess_infer_year(detail_html)
            main_html = extract_main_html(detail_html)
            lines = strip_to_lines(main_html)
            if len(lines) < 40:
                lines = strip_to_lines(detail_html)

            dr = michess_parse_date_range(lines, year)
            if not dr:
                # no date => skip (prevents junk)
                continue

            startDate, endDate = dr

            if not within_window(startDate) or not is_upcoming(endDate):
                continue

            loc = michess_parse_city_state(lines)
            if loc:
                city, state = loc
            else:
                city, state = "Unknown", "MI"

            source_text = normalize_whitespace_text(lines, max_chars=14000)

            out.append({
                "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
                "name": title,
                "startDate": startDate,
                "endDate": endDate,
                "city": city,
                "state": state,
                "sourceId": source["id"],
                "sourceUrl": u,
                "sourceText": source_text,
            })
        except Exception as e:
            print(f"[michess] FAILED {u}: {e}")

        if idx % 10 == 0 or idx == len(urls):
            print(f"[michess] processed {idx}/{len(urls)}")

    return out


# ----------------------------
# Orchestrator
# ----------------------------

def main() -> None:
    all_events: list[dict[str, Any]] = []

    for src in SOURCE_CATALOG:
        try:
            if src["type"] == "uschess_upcoming":
                events = fetch_uschess(src)
            elif src["type"] == "michess_events":
                events = fetch_michess(src)
            else:
                events = []

            print(f"[{src['id']}] fetched {len(events)} events")
            all_events.extend(events)
        except Exception as e:
            print(f"[{src['id']}] FAILED: {e}")

    # dedupe + sort
    all_events = dedupe(all_events)
    all_events.sort(key=lambda e: (e.get("startDate", "9999-99-99"), e.get("name", "")))

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")


if __name__ == "__main__":
    main()
