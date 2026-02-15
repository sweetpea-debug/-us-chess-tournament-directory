#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (US Chess + michess).

Outputs repo-root events.json:
  { "syncedAt": "<iso>", "events": [ ... ] }

Goals:
- Venue is the place name/address (NOT "City, ST")
- Location is always City, ST
- Try to extract: timeControl, entryFee, sections
- Try to extract lat/lon from detail pages so 100-mile filter works

Standard library only.
"""

from __future__ import annotations

import html
import json
import re
import time
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
# Sources
# ----------------------------
SOURCES: list[dict[str, Any]] = [
    {
        "id": "uschess-upcoming",
        "name": "US Chess Upcoming Tournaments",
        "homepage": "https://new.uschess.org/upcoming-tournaments",
        "endpoint": "https://new.uschess.org/upcoming-tournaments",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "homepage": "https://www.michess.org/events",
        "endpoint": "https://www.michess.org/events",
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
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return raw.decode(errors="replace")
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
    s = value.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] if s else "event"

def _strip_html_to_lines(markup: str) -> list[str]:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    markup = re.sub(r"</(p|div|li|h1|h2|h3|h4|tr|td|th|section|article|header|footer)\s*>", "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    out: list[str] = []
    for raw in text.splitlines():
        ln = re.sub(r"\s+", " ", raw).strip()
        if ln:
            out.append(ln)
    return out

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

def parse_location_flexible(loc: str) -> tuple[str, str] | None:
    """
    Accept:
      - 'City, ST'
      - 'City, StateName'
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

def parse_us_chess_date_one(s: str) -> date | None:
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

def parse_us_chess_date_range(s: str) -> tuple[str, str] | None:
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

def extract_lat_lon_from_html(page_html: str) -> tuple[float | None, float | None]:
    # data-lat / data-lng
    m = re.search(r'data-lat=["\'](-?\d+(?:\.\d+)?)["\']', page_html, flags=re.I)
    n = re.search(r'data-lng=["\'](-?\d+(?:\.\d+)?)["\']', page_html, flags=re.I)
    if m and n:
        try:
            return float(m.group(1)), float(n.group(1))
        except ValueError:
            pass

    # latitude/longitude json-ish
    m = re.search(r'"latitude"\s*:\s*"?(?P<lat>-?\d+(?:\.\d+)?)"?', page_html, flags=re.I)
    n = re.search(r'"longitude"\s*:\s*"?(?P<lon>-?\d+(?:\.\d+)?)"?', page_html, flags=re.I)
    if m and n:
        try:
            return float(m.group("lat")), float(n.group("lon"))
        except ValueError:
            pass

    # google maps center=lat,lon
    m = re.search(r"center=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", page_html, flags=re.I)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass

    # q=lat,lon
    m = re.search(r"[?&]q=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", page_html, flags=re.I)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass

    return None, None

def clean_venue(value: str, city: str, state: str) -> str:
    v = re.sub(r"\s+", " ", (value or "")).strip()
    if not v:
        return ""
    # If it’s literally just "City, ST" (or contains it), drop it
    city_state = f"{city}, {state}".lower()
    if v.lower() == city_state:
        return ""
    if v.lower().endswith(city_state):
        v = v[: -len(city_state)].rstrip(" ,;-")
    return v.strip()

def find_field(lines: list[str], labels: list[str]) -> str:
    labels_l = {x.lower().strip() for x in labels}
    for i, ln in enumerate(lines):
        if ln.lower().strip() in labels_l:
            for j in range(i + 1, min(i + 12, len(lines))):
                v = lines[j].strip()
                if not v:
                    continue
                if v.endswith(":") and len(v) <= 28:
                    return ""
                return v
    return ""

def extract_time_control(text: str) -> str:
    # common patterns: G/60;+5, G/90 d30, etc.
    m = re.search(r"\bG\s*/\s*\d+\s*(?:\+\s*\d+|[;,]?\s*d\s*\d+)?\b", text, flags=re.I)
    if m:
        v = re.sub(r"\s+", "", m.group(0)).replace(",", ";").upper()
        v = v.replace("D", "d") if "d" in m.group(0) else v
        return v

    # "Time Control:" label style
    m = re.search(r"(Time Control|Time control)\s*[:\-]\s*([^\n.;]{3,80})", text, flags=re.I)
    if m:
        return m.group(2).strip()

    return ""

def extract_entry_fee(text: str) -> str:
    m = re.search(r"(Entry Fee|Entry fee)\s*[:\-]\s*([^\n.;]{2,80})", text, flags=re.I)
    if m:
        return m.group(2).strip()

    # fallback: first $ amount-ish
    m = re.search(r"\$\s?\d{1,4}(?:\.\d{2})?(?:\s*-\s*\$\s?\d{1,4}(?:\.\d{2})?)?", text)
    if m:
        return m.group(0).replace("  ", " ").strip()

    return ""

def extract_sections(text: str) -> list[str]:
    # "Sections: Open, U1800, ..."
    m = re.search(r"(Sections?|Divisions?)\s*[:\-]\s*([^\n.]{3,200})", text, flags=re.I)
    if m:
        raw = m.group(2).strip()
        parts = [p.strip() for p in re.split(r"[;,/]| and ", raw) if p.strip()]
        # avoid garbage
        cleaned = []
        for p in parts:
            if len(p) > 45:
                continue
            cleaned.append(p)
        return cleaned[:20]
    return []

# ----------------------------
# US Chess
# ----------------------------
def uschess_title_url_map(page_html: str, base_url: str) -> dict[str, str]:
    m: dict[str, str] = {}
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

def parse_uschess_listing(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _strip_html_to_lines(page_html)
    title_url = uschess_title_url_map(page_html, source["homepage"])

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        # US Chess listing pages typically have headings for event titles;
        # this heuristic works well with the stripped lines.
        title = lines[i].strip()
        if len(title) < 6:
            i += 1
            continue

        # look ahead for location + date
        loc = None
        dr = None
        for j in range(i + 1, min(i + 10, len(lines))):
            if loc is None:
                loc_try = parse_location_flexible(lines[j])
                if loc_try:
                    loc = loc_try
                    continue
            if dr is None:
                dr_try = parse_us_chess_date_range(lines[j])
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
                "summary": "",
                "sourceId": source["id"],
                "sourceUrl": event_url,
            }
        )

        i += 1

    # Dedupe inside a page (listing text repeats sometimes)
    return dedupe(out)

def enrich_uschess_event(event: dict[str, Any]) -> dict[str, Any]:
    url = event.get("sourceUrl") or ""
    if not url or "new.uschess.org" not in url:
        return event

    detail_html = fetch_text(url)
    lines = _strip_html_to_lines(detail_html)
    blob = " ".join(lines)
    blob = re.sub(r"\s+", " ", blob).strip()

    # lat/lon
    lat, lon = extract_lat_lon_from_html(detail_html)
    if lat is not None and lon is not None:
        event["lat"] = lat
        event["lon"] = lon

    # venue (try labeled fields first)
    venue = find_field(lines, ["Venue", "Location", "Site", "Address"])
    # Sometimes the “Location” field is actually "City, ST" — strip it out
    venue = clean_venue(venue, event.get("city", ""), event.get("state", ""))
    event["venue"] = venue

    # time control / entry fee / sections from any text
    tc = extract_time_control(blob)
    fee = extract_entry_fee(blob)
    secs = extract_sections(blob)

    if tc:
        event["timeControl"] = tc
    if fee:
        event["entryFee"] = fee
    if secs:
        event["sections"] = secs

    # Short “summary” (card line)
    # Prefer time control; otherwise a short chunk mentioning rounds/sections/format
    if event.get("timeControl"):
        event["summary"] = event["timeControl"]
    else:
        m = re.search(r"\b(\d+)\s*(round|rnd)\b.{0,60}", blob, flags=re.I)
        if m:
            event["summary"] = m.group(0).strip()[:90]

    return event

def fetch_uschess(source: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for page in range(0, 80):
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        html_text = fetch_text(url)
        page_events = parse_uschess_listing(html_text, source)
        print(f"[uschess-upcoming] page={page} parsed={len(page_events)}")
        if not page_events and page > 0:
            break
        events.extend(page_events)

    events = dedupe(events)

    # Enrich with detail pages (limited concurrency)
    total = len(events)
    if total == 0:
        return events

    max_enrich = min(total, 900)  # safety cap
    print(f"[uschess-upcoming] enriching {max_enrich}/{total} ...")

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(enrich_uschess_event, events[i]): i for i in range(max_enrich)}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 20 == 0:
                print(f"[uschess-upcoming] enriching {done}/{max_enrich} ...")
            try:
                enriched.append(fut.result())
            except Exception as e:
                # keep the base event even if enrichment fails
                idx = futures[fut]
                print(f"[uschess-upcoming] enrich FAILED: {events[idx].get('sourceUrl')} :: {e}")
                enriched.append(events[idx])

    # If there were more than max_enrich, append the rest un-enriched
    if max_enrich < total:
        enriched.extend(events[max_enrich:])

    return enriched

# ----------------------------
# michess
# ----------------------------
def michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)
    for path in re.findall(r'(/event-details/[a-z0-9\-]+-\d+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))
    return sorted(urls)

def infer_year_from_title(title: str) -> int:
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    t = date.today()
    return t.year

def parse_michess_date_range(line: str, title: str) -> tuple[str, str] | None:
    s = line.strip()
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

    y = infer_year_from_title(title)
    d1 = int(m.group(2))
    d2 = int(m.group(4))

    try:
        start = date(y, mon1, d1)
        end = date(y, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

def parse_michess_detail(detail_html: str, url: str, source: dict[str, Any]) -> dict[str, Any] | None:
    lines = _strip_html_to_lines(detail_html)

    # Title: first non-generic line
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

    # Date
    startDate = endDate = None
    for ln in lines[:200]:
        dr = parse_michess_date_range(ln, title)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    # Location line with "United States"
    venue_line = ""
    city = "Unknown"
    state = "US"
    for ln in lines[:260]:
        if "United States" in ln and "," in ln:
            venue_line = ln.strip()
            mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", venue_line)
            if mloc:
                city = mloc.group(1).strip()
                state = mloc.group(2).strip()
            break

    # venue: michess location line often contains address and also city/state; keep it as venue but strip trailing "United States"
    venue = venue_line.replace("United States", "").strip(" ,")
    venue = clean_venue(venue, city, state)

    blob = " ".join(lines)
    blob = re.sub(r"\s+", " ", blob).strip()

    # time control / entry fee / sections
    tc = find_field(lines, ["Time Control:", "Time control:", "Time Control", "Time control"])
    fee = find_field(lines, ["Entry Fee:", "Entry fee:", "Entry Fee", "Entry fee"])
    fmt_sections = find_field(lines, ["Sections:", "Section:", "Sections", "Section"])

    # fallback heuristics
    tc = tc or extract_time_control(blob)
    fee = fee or extract_entry_fee(blob)
    sections = extract_sections(blob)
    if fmt_sections:
        # also parse label sections
        parts = [p.strip() for p in re.split(r"[;,/]| and ", fmt_sections) if p.strip()]
        if parts:
            sections = parts[:20]

    # lat/lon
    lat, lon = extract_lat_lon_from_html(detail_html)

    # summary for cards
    summary = ""
    if tc:
        summary = tc
    else:
        m = re.search(r"\b(\d+)\s*(round|rnd)\b.{0,60}", blob, flags=re.I)
        if m:
            summary = m.group(0).strip()[:90]

    return {
        "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "venue": venue,
        "lat": lat,
        "lon": lon,
        "entryFee": fee or "",
        "sections": sections or [],
        "timeControl": tc or "",
        "summary": summary,
        "sourceId": source["id"],
        "sourceUrl": url,
    }

def fetch_michess(source: dict[str, Any]) -> list[dict[str, Any]]:
    listing_html = fetch_text(source["endpoint"])
    urls = michess_extract_detail_urls(listing_html, source["homepage"])
    print(f"[michess] /events contained {len(urls)} event-details urls")

    out: list[dict[str, Any]] = []
    for u in urls:
        try:
            detail_html = fetch_text(u)
            ev = parse_michess_detail(detail_html, u, source)
            if ev:
                out.append(ev)
            # small polite delay (michess is small anyway)
            time.sleep(0.15)
        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

    return out

# ----------------------------
# Main
# ----------------------------
def main() -> None:
    all_events: list[dict[str, Any]] = []

    for src in SOURCES:
        try:
            if src["id"] == "uschess-upcoming":
                events = fetch_uschess(src)
            elif src["id"] == "michess":
                events = fetch_michess(src)
            else:
                events = []
            print(f"[{src['id']}] fetched {len(events)} raw events")
            all_events.extend(events)
        except Exception as e:
            print(f"[{src['id']}] FAILED: {e}")

    # upcoming only
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
