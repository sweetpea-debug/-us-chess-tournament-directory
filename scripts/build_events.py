#!/usr/bin/env python3
"""
Daily ingest for chess tournaments.

TEST MODE SOURCES:
  1) US Chess Upcoming Tournaments (new.uschess.org/upcoming-tournaments) + detail page enrich
  2) Michigan Chess Association events (michess.org/events -> /event-details/... pages)

Output (repo root):
  - events.json: { "syncedAt": "<iso>", "events": [...] }
  - geocode_cache.json: cached lat/lon for (city,state) to make the 100-mile filter work

Notes:
  - Standard library only (no external deps).
  - Geocoding uses Nominatim (OpenStreetMap). We cache results and throttle requests.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlencode
from urllib.request import Request, urlopen


# ----------------------------
# Paths
# ----------------------------

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]  # scripts/ -> repo root
OUTPUT_EVENTS = ROOT / "events.json"
GEOCODE_CACHE_PATH = ROOT / "geocode_cache.json"


# ----------------------------
# Sources
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

DEFAULT_TIMEOUT_SECS = 30
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
            return raw.decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        raise RuntimeError(f"Fetch failed for {url}: {e}") from e


# ----------------------------
# HTML -> text lines
# ----------------------------

def _strip_html_to_lines(markup: str) -> list[str]:
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


def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:90] if value else "event"


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


# ----------------------------
# Date + location parsing (US Chess)
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

def _parse_us_chess_date_one(s: str) -> date | None:
    s = s.strip()
    s = re.sub(r"^[A-Za-z]+,\s*", "", s)  # remove weekday
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
      - City, ST
      - City, StateName
      - City, ST, StateName
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
        maybe_abbr = parts[1]
        if re.fullmatch(r"[A-Z]{2}", maybe_abbr):
            return city, maybe_abbr
        abbr = US_STATE_ABBR.get(parts[-1].lower())
        return (city, abbr) if abbr else None

    return None


# ----------------------------
# US Chess list-page parsing
# ----------------------------

def _clean_title_line(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^#+\s*", "", s)
    s = s.replace("】", "").strip()
    return s

def _uschess_title_to_url_map(page_html: str, base_url: str) -> dict[str, str]:
    """
    Map by normalized title -> full URL for detail page.
    """
    m: dict[str, str] = {}

    # Typical: <h3 ...><a href="/some-slug">Title</a></h3>
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S,
    ):
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        m[title.lower()] = urljoin(base_url, href)

    return m

def parse_uschess_upcoming(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _strip_html_to_lines(page_html)
    title_url = _uschess_title_to_url_map(page_html, source["homepage"])

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("###"):
            i += 1
            continue

        title = _clean_title_line(lines[i])

        loc: tuple[str, str] | None = None
        dr: tuple[str, str] | None = None

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
            i += 1
            continue

        city, state = loc
        startDate, endDate = dr
        event_url = title_url.get(title.lower(), source["homepage"])

        out.append(
            {
                "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
                "name": title,
                "startDate": startDate,
                "endDate": endDate,
                "city": city,
                "state": state,
                "venue": "",
                "lat": None,
                "lon": None,
                "entryFee": "",
                "sections": [],
                "timeControl": "",
                "sourceId": source["id"],
                "sourceUrl": event_url,
            }
        )

        i += 1

    return out


# ----------------------------
# US Chess detail-page enrich (heuristics)
# ----------------------------

_LABEL_STOP = {"contact", "share", "website", "register", "registration", "director", "td", "tournament director"}

def _find_after_label(lines: list[str], labels: list[str], max_lookahead: int = 10) -> str:
    labels_l = {l.lower().strip(":") for l in labels}
    for idx, ln in enumerate(lines):
        key = ln.lower().strip().rstrip(":")
        if key in labels_l:
            for j in range(idx + 1, min(idx + 1 + max_lookahead, len(lines))):
                v = lines[j].strip()
                if not v:
                    continue
                if v.lower().strip().rstrip(":") in labels_l:
                    return ""
                if v.lower().strip().rstrip(":") in _LABEL_STOP:
                    return ""
                return v
    return ""

def _extract_sections(lines: list[str]) -> list[str]:
    # Try label-based first
    sections_raw = _find_after_label(lines, ["Sections", "Section(s)"], max_lookahead=12)
    if sections_raw:
        parts = re.split(r"[•|,;/]+", sections_raw)
        parts = [p.strip() for p in parts if p.strip()]
        return parts[:20]

    # Fallback: look for "Sections:" inline
    for ln in lines:
        m = re.search(r"\bSections?:\s*(.+)$", ln, flags=re.I)
        if m:
            parts = re.split(r"[•|,;/]+", m.group(1))
            parts = [p.strip() for p in parts if p.strip()]
            return parts[:20]

    return []

def _extract_entry_fee(lines: list[str]) -> str:
    fee = _find_after_label(lines, ["Entry fee", "Entry Fee", "Fees", "Fee"], max_lookahead=8)
    if fee:
        return fee

    # Sometimes inline
    for ln in lines[:300]:
        m = re.search(r"\bEntry fee\b[:\s\-]*([^\.;]+)", ln, flags=re.I)
        if m:
            return m.group(1).strip()

    # Look for common fee patterns near top
    for ln in lines[:300]:
        if "$" in ln and ("entry" in ln.lower() or "fee" in ln.lower()):
            return ln.strip()

    return ""

def _extract_time_control(lines: list[str]) -> str:
    tc = _find_after_label(lines, ["Time control", "Time Control"], max_lookahead=8)
    if tc:
        return tc

    # Common chess notation patterns: G/xx;dyy, etc.
    patterns = [
        r"\bG\s*/\s*\d+\s*(?:\+|;|,)?\s*(?:d\s*)?\d+\b",
        r"\b\d+\s*\+\s*\d+\b",
        r"\b(?:G|SD)\s*/\s*\d+\b",
    ]
    text = " ".join(lines[:400])
    text = re.sub(r"\s+", " ", text)
    hits: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            val = m.group(0).strip()
            if val and val.lower() not in {"g/"}:
                hits.append(val)
    # Return the most "specific" hit (longest)
    hits = sorted(set(hits), key=lambda s: (-len(s), s))
    return hits[0] if hits else ""

def _extract_venue(lines: list[str], city: str, state: str) -> str:
    venue = _find_after_label(lines, ["Venue", "Site", "Location"], max_lookahead=20)
    if venue:
        # Remove accidental "City, ST" repeats
        venue = venue.strip().strip(",")
        if venue.lower() == f"{city}, {state}".lower():
            return ""
        return venue

    # Fallback: find an address-ish line (street number)
    for ln in lines[:400]:
        if re.search(r"\b\d{2,6}\s+\w+", ln) and (state in ln or "United States" in ln):
            cleaned = ln.strip().strip(",")
            if cleaned.lower() == f"{city}, {state}".lower():
                continue
            return cleaned[:200]

    return ""

def enrich_uschess_event(event: dict[str, Any]) -> dict[str, Any]:
    url = event.get("sourceUrl") or ""
    if not url or "new.uschess.org" not in url:
        return event

    try:
        detail_html = fetch_text(url)
    except Exception:
        return event

    lines = _strip_html_to_lines(detail_html)

    venue = _extract_venue(lines, event["city"], event["state"])
    fee = _extract_entry_fee(lines)
    tc = _extract_time_control(lines)
    sections = _extract_sections(lines)

    # Some pages include a better title in og:title
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        og_title = html.unescape(m.group(1)).strip()
        if og_title and len(og_title) > 4:
            event["name"] = og_title

    if venue:
        event["venue"] = venue
    if fee:
        event["entryFee"] = fee
    if tc:
        event["timeControl"] = tc
    if sections:
        event["sections"] = sections

    return event


# ----------------------------
# Michess parsing
# ----------------------------

MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

def _michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    for path in re.findall(r'(/event-details/[a-z0-9\-]+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)

def _infer_year_from_text(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    return date.today().year

def _parse_michess_date_range(line: str, year_hint: int) -> tuple[str, str] | None:
    s = line.strip()
    m = re.match(
        r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
        s,
    )
    if not m:
        # Sometimes single-day like: "Sat, Feb 14"
        m2 = re.match(r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$", s)
        if not m2:
            return None
        mon = MONTHS_ABBR.get(m2.group(1).lower())
        if not mon:
            return None
        d = int(m2.group(2))
        try:
            dt = date(year_hint, mon, d)
            return dt.isoformat(), dt.isoformat()
        except ValueError:
            return None

    mon1 = MONTHS_ABBR.get(m.group(1).lower())
    mon2 = MONTHS_ABBR.get(m.group(3).lower())
    if not mon1 or not mon2:
        return None
    d1 = int(m.group(2))
    d2 = int(m.group(4))

    try:
        start = date(year_hint, mon1, d1)
        end = date(year_hint, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

def _michess_title_from_html(detail_html: str) -> str:
    # og:title first
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        return html.unescape(m.group(1)).strip()

    # <title> fallback
    m2 = re.search(r"<title>(.*?)</title>", detail_html, flags=re.I | re.S)
    if m2:
        t = re.sub(r"\s+", " ", html.unescape(m2.group(1))).strip()
        return t

    return ""

def parse_michess_event_detail(detail_html: str, source: dict[str, Any], url: str) -> dict[str, Any] | None:
    lines = _strip_html_to_lines(detail_html)
    title = _michess_title_from_html(detail_html).strip()

    if title:
        # common suffix cleanup
        title = re.sub(r"\s*\|\s*Michigan Chess Association\s*$", "", title, flags=re.I).strip()

    if not title:
        # fallback: first decent line
        for ln in lines[:80]:
            if len(ln) >= 6 and ln.lower() not in {"events", "event"}:
                title = ln.strip()
                break
    if not title:
        return None

    year_hint = _infer_year_from_text(detail_html)

    startDate = endDate = None
    for ln in lines[:200]:
        dr = _parse_michess_date_range(ln, year_hint)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    # Location: find "City, ST" somewhere near a map/address line
    city = "Unknown"
    state = "US"
    venue_line = ""

    for ln in lines[:400]:
        mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if mloc:
            city = mloc.group(1).strip()
            state = mloc.group(2).strip()
            # Try to use a venue-ish line (often includes address)
            if re.search(r"\b\d{2,6}\s+\w+", ln):
                venue_line = ln.strip().strip(",")
            break

    # Field extraction (labels)
    def after(label: str) -> str:
        return _find_after_label(lines, [label], max_lookahead=12)

    fmt = after("Format")
    tc = after("Time Control")
    fee = after("Entry Fee")

    # Michess sometimes has "Sections" too
    sections = []
    sec_raw = after("Sections")
    if sec_raw:
        parts = re.split(r"[•|,;/]+", sec_raw)
        sections = [p.strip() for p in parts if p.strip()][:20]

    return {
        "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "venue": venue_line,
        "lat": None,
        "lon": None,
        "entryFee": fee or "",
        "sections": sections,
        "timeControl": tc or "",
        "sourceId": source["id"],
        "sourceUrl": url,
    }

def parse_michess_events(listing_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    base = source["homepage"]
    urls = _michess_extract_detail_urls(listing_html, base)
    print(f"[michess] found {len(urls)} event-details urls")

    out: list[dict[str, Any]] = []
    for u in urls:
        try:
            detail_html = fetch_text(u)
            ev = parse_michess_event_detail(detail_html, source, u)
            if ev:
                out.append(ev)
        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

    return out


# ----------------------------
# Geocoding (for distance filter)
# ----------------------------

def load_geocode_cache() -> dict[str, dict[str, float]]:
    if not GEOCODE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_geocode_cache(cache: dict[str, dict[str, float]]) -> None:
    GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")

def geocode_city_state(city: str, state: str) -> tuple[float, float] | None:
    # Nominatim policy: include a valid UA; keep requests low
    query = f"{city}, {state}, USA"
    params = urlencode({"q": query, "format": "jsonv2", "limit": "1"})
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=DEFAULT_TIMEOUT_SECS) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    rows = json.loads(raw)
    if not rows:
        return None
    return float(rows[0]["lat"]), float(rows[0]["lon"])

def attach_latlon(events: list[dict[str, Any]], max_new_requests: int = 60) -> list[dict[str, Any]]:
    """
    Fill missing lat/lon using cached city/state geocodes.
    We throttle + cap new requests so Actions doesn't run forever.
    """
    cache = load_geocode_cache()
    new_requests = 0

    for ev in events:
        if ev.get("lat") is not None and ev.get("lon") is not None:
            continue

        city = (ev.get("city") or "").strip()
        state = (ev.get("state") or "").strip()
        if not city or not state or state == "US":
            continue

        key = f"{city.lower()}|{state.upper()}"
        if key in cache:
            ev["lat"] = cache[key]["lat"]
            ev["lon"] = cache[key]["lon"]
            continue

        if new_requests >= max_new_requests:
            continue

        try:
            got = geocode_city_state(city, state)
            if got:
                lat, lon = got
                cache[key] = {"lat": lat, "lon": lon}
                ev["lat"] = lat
                ev["lon"] = lon
        except Exception:
            pass

        new_requests += 1
        time.sleep(1.1)  # be polite to Nominatim

    save_geocode_cache(cache)
    print(f"[geocode] cache size={len(cache)} new_requests={new_requests}")
    return events


# ----------------------------
# Orchestrator
# ----------------------------

def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    parser = source["parser"]

    if parser == "uschess_upcoming":
        events: list[dict[str, Any]] = []
        for page in range(0, 80):
            url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
            html_text = fetch_text(url)
            page_events = parse_uschess_upcoming(html_text, source)
            print(f"[uschess-upcoming] page={page} parsed={len(page_events)}")
            if not page_events and page > 0:
                break
            events.extend(page_events)

        # Enrich detail pages for better venue/fee/time control/sections
        enriched: list[dict[str, Any]] = []
        total = len(events)
        for idx, ev in enumerate(events, start=1):
            enriched.append(enrich_uschess_event(ev))
            if idx % 50 == 0 or idx == total:
                print(f"[uschess-upcoming] enriching {idx}/{total} ...")

        return enriched

    if parser == "michess_events":
        listing_html = fetch_text(source["endpoint"])
        return parse_michess_events(listing_html, source)

    return []


def main() -> None:
    all_events: list[dict[str, Any]] = []

    for source in SOURCE_CATALOG:
        try:
            events = fetch_source(source)
            print(f"[{source['id']}] fetched {len(events)} raw events")
            all_events.extend(events)
        except Exception as e:
            print(f"[{source['id']}] FAILED: {e}")

    # Upcoming only
    all_events = [e for e in all_events if is_upcoming(e)]

    # Dedupe
    all_events = dedupe(all_events)

    # Attach lat/lon so the 100-mile filter works
    all_events = attach_latlon(all_events, max_new_requests=60)

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_EVENTS.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_EVENTS} with {len(all_events)} events")


if __name__ == "__main__":
    main()
