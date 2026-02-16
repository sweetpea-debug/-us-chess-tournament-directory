#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (US Chess + MiChess).

Outputs repo-root events.json as:
  { "syncedAt": "<iso>", "events": [ ... ] }

Notes:
- Standard library only.
- US Chess "Upcoming Tournaments" is paginated (?page=0,1,2...)
- MiChess requires following /event-details/... pages.
- Best-effort parsing of: venue, time control, entry fee, sections.
- Optional geocoding (OpenStreetMap Nominatim) with caching + throttling:
    - cache file: repo-root/geocode_cache.json
    - caps new lookups per run to keep Actions fast and polite
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


# ----------------------------
# Paths
# ----------------------------

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]  # scripts/ -> repo root
OUTPUT_PATH = ROOT / "events.json"
GEOCODE_CACHE_PATH = ROOT / "geocode_cache.json"


# ----------------------------
# Sources (these two for now)
# ----------------------------

SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "uschess-upcoming",
        "name": "US Chess",
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
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    markup = re.sub(
        r"</(p|div|li|h1|h2|h3|h4|tr|td|th|section|article|header|footer)\s*>",
        "\n",
        markup,
        flags=re.I,
    )
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


def _normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# ----------------------------
# Shared parsing helpers
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
    s = _normalize_space(s)
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

def _parse_us_chess_date_range(s: str) -> tuple[str, str] | None:
    s = _normalize_space(s)
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
      - 'City, ST, StateName'  (sometimes appears)
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
# Best-effort field extraction
# ----------------------------

_TC_PATTERNS = [
    # Common US notation
    r"\bG\s*/\s*\d+\s*(?:\+\s*\d+|;\s*\+?\d+)?\b",
    r"\bG\s*\d+\s*(?:\+\s*\d+|;\s*\+?\d+)?\b",
    r"\b(\d+\s*\+\s*\d+)\b",  # 90+30, 25+5, etc.
    r"\b(\d+\/\d+)\b",        # 40/90 etc (less common)
    r"\b(delay|increment)\b.*?\b\d+\b",
]

_FEE_PATTERNS = [
    r"\bEntry fee[:\s]*([^.\n;]+)",
    r"\bEntry[:\s]*([^.\n;]+)",
    r"\bFee[:\s]*([^.\n;]+)",
    r"\bRegistration fee[:\s]*([^.\n;]+)",
    r"\b\$ ?\d+(?:\.\d{2})?\b(?:\s*-\s*\$ ?\d+(?:\.\d{2})?)?",
]

_SECTIONS_HINTS = [
    r"\bSections?[:\s]*([^.\n]+)",
    r"\b(Open|Reserve|Novice|Scholastic|U\d{3,4}|K-?12|K-?8|K-?6)\b",
]

def extract_time_control(text: str) -> str:
    t = _normalize_space(text)
    if not t:
        return ""
    for pat in _TC_PATTERNS:
        m = re.search(pat, t, flags=re.I)
        if m:
            val = m.group(0)
            val = re.sub(r"\s+", "", val).replace(";", ";+")
            return val.upper()
    return ""

def extract_entry_fee(text: str) -> str:
    t = _normalize_space(text)
    if not t:
        return ""
    for pat in _FEE_PATTERNS:
        m = re.search(pat, t, flags=re.I)
        if not m:
            continue
        val = m.group(1) if m.lastindex else m.group(0)
        val = _normalize_space(val)
        # avoid capturing giant paragraphs
        return val[:120]
    return ""

def extract_sections(text: str) -> list[str]:
    t = _normalize_space(text)
    if not t:
        return []
    # Try explicit "Sections:" first
    m = re.search(_SECTIONS_HINTS[0], t, flags=re.I)
    if m:
        raw = m.group(1)
        raw = re.sub(r"\s+", " ", raw).strip()
        parts = re.split(r"[,\|/]+", raw)
        parts = [p.strip() for p in parts if p.strip()]
        return parts[:20]

    # Otherwise, gather common section tokens
    found: list[str] = []
    for m2 in re.finditer(_SECTIONS_HINTS[1], t, flags=re.I):
        tok = m2.group(0).strip()
        tok = tok.replace("k-", "K-").replace("K-", "K-")
        if tok.upper().startswith("U") and tok[1:].isdigit():
            tok = tok.upper()
        if tok.lower() == "open":
            tok = "Open"
        if tok not in found:
            found.append(tok)
        if len(found) >= 12:
            break
    return found


# ----------------------------
# Geocoding (optional)
# ----------------------------

def _load_geocode_cache() -> dict[str, dict[str, float]]:
    if not GEOCODE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_geocode_cache(cache: dict[str, dict[str, float]]) -> None:
    GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")

def geocode_cached(query: str, cache: dict[str, dict[str, float]], new_lookup_budget: list[int]) -> tuple[float | None, float | None]:
    """
    query: a string like "Detroit, MI" or "123 Main St, Detroit, MI"
    new_lookup_budget: mutable [remaining] counter; decremented on actual HTTP lookups.
    """
    q = _normalize_space(query)
    if not q:
        return None, None
    if q in cache:
        return cache[q].get("lat"), cache[q].get("lon")

    # If we're out of budget, skip.
    if new_lookup_budget[0] <= 0:
        return None, None

    params = {
        "q": q,
        "format": "jsonv2",
        "countrycodes": "us",
        "limit": "1",
    }
    url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        # Be polite
        time.sleep(1.05)
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        rows = json.loads(raw)
        if not rows:
            return None, None
        lat = float(rows[0]["lat"])
        lon = float(rows[0]["lon"])
        cache[q] = {"lat": lat, "lon": lon}
        new_lookup_budget[0] -= 1
        return lat, lon
    except Exception:
        return None, None


# ----------------------------
# US Chess: listing parsing + detail enrichment
# ----------------------------

def _uschess_title_to_url_map(page_html: str, base_url: str) -> dict[str, str]:
    m: dict[str, str] = {}
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S,
    ):
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = _normalize_space(title)
        if not title:
            continue
        full = urljoin(base_url, href)
        m[title.lower()] = full
    return m


def parse_uschess_upcoming_listing(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _strip_html_to_lines(page_html)
    title_url = _uschess_title_to_url_map(page_html, source["homepage"])

    out: list[dict[str, Any]] = []

    i = 0
    while i < len(lines):
        if not lines[i].startswith("###"):
            i += 1
            continue

        title = re.sub(r"^#+\s*", "", lines[i]).strip()
        title = _normalize_space(title)
        if len(title) < 4:
            i += 1
            continue

        loc = None
        dr = None

        for j in range(i + 1, min(i + 10, len(lines))):
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

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": state,
            "venue": "",        # enriched later
            "lat": None,
            "lon": None,
            "entryFee": "",
            "sections": [],
            "timeControl": "",
            "sourceId": source["id"],
            "sourceUrl": event_url,
        })

        i += 1

    return out


def parse_uschess_detail(detail_html: str) -> dict[str, Any]:
    """
    Best-effort extraction of venue / fee / tc / sections from a US Chess event page.
    This is intentionally heuristic and may improve over time.
    """
    lines = _strip_html_to_lines(detail_html)
    blob = _normalize_space(" ".join(lines))

    # Venue-like: try a line that contains an address-ish pattern or "Location:"
    venue = ""
    # Prefer explicit "Location:" label if present
    m_loc = re.search(r"\bLocation:\s*([^.\n]{10,200})", blob, flags=re.I)
    if m_loc:
        venue = _normalize_space(m_loc.group(1))[:200]
    else:
        # Otherwise grab first line that looks address-y
        for ln in lines[:250]:
            if re.search(r"\b\d{2,5}\s+\w+", ln) and re.search(r"\b[A-Z]{2}\b", ln):
                venue = _normalize_space(ln)[:200]
                break

    tc = extract_time_control(blob)
    fee = extract_entry_fee(blob)
    sections = extract_sections(blob)

    return {
        "venue": venue,
        "timeControl": tc,
        "entryFee": fee,
        "sections": sections,
    }


def fetch_uschess_events(source: dict[str, Any], geocode: bool) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    # 1) Listing pages
    for page in range(0, 80):
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        html_text = fetch_text(url)
        page_events = parse_uschess_upcoming_listing(html_text, source)
        print(f"[uschess-upcoming] page={page} parsed={len(page_events)}")
        if not page_events and page > 0:
            break
        events.extend(page_events)

    print(f"[uschess-upcoming] fetched {len(events)} listing events")

    # 2) Enrich details (cap to keep Actions reasonable)
    # If you want ALL enriched, raise this number later.
    ENRICH_CAP = int(os.environ.get("USCHESS_ENRICH_CAP", "250"))
    cache = _load_geocode_cache()
    budget = [int(os.environ.get("GEOCODE_BUDGET", "25"))]  # new lookups per run

    for idx, ev in enumerate(events[:ENRICH_CAP], start=1):
        try:
            detail_html = fetch_text(ev["sourceUrl"])
            extra = parse_uschess_detail(detail_html)

            for k, v in extra.items():
                if k == "sections":
                    if v and isinstance(v, list):
                        ev["sections"] = v
                else:
                    if v and isinstance(v, str):
                        ev[k] = v

            # Geocode: prefer venue + city/state if venue exists
            if geocode:
                q = ""
                if ev.get("venue"):
                    q = f"{ev['venue']}, {ev['city']}, {ev['state']}"
                else:
                    q = f"{ev['city']}, {ev['state']}"
                lat, lon = geocode_cached(q, cache, budget)
                ev["lat"], ev["lon"] = lat, lon

        except Exception as e:
            print(f"[uschess-upcoming] enrich FAILED {ev.get('sourceUrl')}: {e}")

        if idx % 20 == 0:
            print(f"[uschess-upcoming] enriching {idx}/{min(len(events), ENRICH_CAP)} ...")

    if geocode:
        _save_geocode_cache(cache)

    return events


# ----------------------------
# MiChess: follow event-details pages
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

def _infer_year_from_title(title: str) -> int:
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    today = date.today()
    return today.year

def _parse_michess_date_range(line: str, title: str) -> tuple[str, str] | None:
    s = _normalize_space(line)
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
    y = _infer_year_from_title(title)
    try:
        start = date(y, mon1, int(m.group(2)))
        end = date(y, mon2, int(m.group(4)))
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

def _grab_value_after_label(lines: list[str], label: str) -> str:
    label_lower = label.lower().strip()
    for i, ln in enumerate(lines):
        if ln.strip().lower() == label_lower:
            for j in range(i + 1, min(i + 12, len(lines))):
                v = lines[j].strip()
                if not v:
                    continue
                if v.endswith(":") and len(v) <= 30:
                    return ""
                return _normalize_space(v)
    return ""

def parse_michess_event_detail(detail_html: str, source: dict[str, Any], url: str) -> dict[str, Any] | None:
    lines = _strip_html_to_lines(detail_html)

    # Title: first decent line
    title = ""
    for ln in lines[:120]:
        low = ln.lower().strip()
        if low in {"events", "event", "submit event"}:
            continue
        if len(ln.strip()) >= 6:
            title = ln.strip()
            break
    if not title:
        return None

    startDate = endDate = None
    for ln in lines[:200]:
        dr = _parse_michess_date_range(ln, title)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    # Venue/location line (often includes full address text with "United States")
    venue_line = ""
    city = "Unknown"
    state = "US"
    for ln in lines[:350]:
        if "United States" in ln and "," in ln:
            venue_line = _normalize_space(ln)[:240]
            mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", venue_line)
            if mloc:
                city = mloc.group(1).strip()
                state = mloc.group(2).strip()
            break

    fmt = _grab_value_after_label(lines, "Format:")
    tc = _grab_value_after_label(lines, "Time Control:")
    fee = _grab_value_after_label(lines, "Entry Fee:")
    sections = _grab_value_after_label(lines, "Sections:")

    # Normalize sections into a list
    sections_list: list[str] = []
    if sections:
        parts = re.split(r"[,\|/]+", sections)
        sections_list = [p.strip() for p in parts if p.strip()][:20]

    # If tc/fee are blank, do heuristic extraction from page text
    blob = _normalize_space(" ".join(lines))
    if not tc:
        tc = extract_time_control(blob)
    if not fee:
        fee = extract_entry_fee(blob)
    if not sections_list:
        sections_list = extract_sections(blob)

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
        "entryFee": fee,
        "sections": sections_list,
        "timeControl": tc,
        "sourceId": source["id"],
        "sourceUrl": url,
    }

def fetch_michess_events(source: dict[str, Any], geocode: bool) -> list[dict[str, Any]]:
    listing_html = fetch_text(source["endpoint"])
    urls = _michess_extract_detail_urls(listing_html, source["homepage"])
    print(f"[michess] found {len(urls)} event-details urls")
    if urls[:3]:
        print("[michess] sample urls:", urls[:3])

    cache = _load_geocode_cache()
    budget = [int(os.environ.get("GEOCODE_BUDGET", "25"))]  # shared cap per run

    out: list[dict[str, Any]] = []
    for u in urls:
        try:
            detail_html = fetch_text(u)
            ev = parse_michess_event_detail(detail_html, source, u)
            if ev:
                if geocode:
                    q = ""
                    if ev.get("venue"):
                        q = f"{ev['venue']}, {ev['city']}, {ev['state']}"
                    else:
                        q = f"{ev['city']}, {ev['state']}"
                    lat, lon = geocode_cached(q, cache, budget)
                    ev["lat"], ev["lon"] = lat, lon
                out.append(ev)
        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

    if geocode:
        _save_geocode_cache(cache)

    return out


# ----------------------------
# Orchestrator
# ----------------------------

def fetch_source(source: dict[str, Any], geocode: bool) -> list[dict[str, Any]]:
    parser = source["parser"]
    if parser == "uschess_upcoming":
        return fetch_uschess_events(source, geocode=geocode)
    if parser == "michess_events":
        return fetch_michess_events(source, geocode=geocode)
    return []

def main() -> None:
    # Turn on geocoding by setting GEOCODE=1 in your workflow/env (recommended after things look good)
    geocode = os.environ.get("GEOCODE", "0") == "1"

    all_events: list[dict[str, Any]] = []

    for source in SOURCE_CATALOG:
        try:
            events = fetch_source(source, geocode=geocode)
            print(f"[{source['id']}] fetched {len(events)} raw events")
            all_events.extend(events)
        except Exception as e:
            print(f"[{source['id']}] FAILED: {e}")

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
