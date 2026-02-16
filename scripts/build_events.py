#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (TEST MODE).

Sources:
  1) US Chess "Upcoming Tournaments" listing (new.uschess.org/upcoming-tournaments)
     - paginated (?page=0..)
     - for each listing item, fetches the event detail page and extracts:
       name, dates, city/state, venue, time control (best effort), entry fee (best effort),
       sections (best effort), lat/lon (if present in structured data / map links)

  2) Michigan Chess Association events (michess.org/events)
     - extracts /event-details/... URLs from listing
     - fetches each detail page and extracts similar fields

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
# Sources (TEST MODE)
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
# Generic helpers
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

def strip_html(markup: str) -> str:
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

def parse_location_flexible(loc: str) -> Optional[tuple[str, str]]:
    # Accept: "City, ST" or "City, StateName" or "City, ST, StateName"
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

def extract_json_ld(html_text: str) -> list[Any]:
    blocks: list[Any] = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, flags=re.I | re.S):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            blocks.append(data)
        except Exception:
            continue
    return blocks

def find_lat_lon_from_text(text: str) -> Optional[tuple[float, float]]:
    # common map patterns: @lat,lon or q=lat,lon
    m = re.search(r"@(-?\d+\.\d+),\s*(-?\d+\.\d+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None

def normalize_venue(venue: str, city: str, state: str) -> str:
    v = (venue or "").strip()
    if not v:
        return ""
    # remove trailing ", City, ST" if present
    tail = f"{city}, {state}"
    v = re.sub(r"\s+", " ", v).strip()
    v = re.sub(rf",?\s*{re.escape(tail)}\s*$", "", v, flags=re.I).strip(" ,")
    return v


# ----------------------------
# US Chess: listing -> detail
# ----------------------------
def uschess_extract_listing_items(listing_html: str, base_url: str) -> list[dict[str, str]]:
    """
    Returns list items: {title, url, location_line, date_line}
    Uses local context around each title link in the listing HTML.
    """
    items: list[dict[str, str]] = []

    # Get title links (most stable hook)
    for href, inner in re.findall(r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h3>", listing_html, flags=re.I | re.S):
        title = strip_html(inner)
        if not title or len(title) < 4:
            continue
        url = urljoin(base_url, href)

        # Use snippet around this match to find location/date lines
        # (not perfect, but good enough to seed detail fetch)
        idx = listing_html.lower().find(href.lower())
        snippet = listing_html[max(0, idx - 1200): idx + 2000]
        lines = strip_html_to_lines(snippet)

        location_line = ""
        date_line = ""
        # heuristic: find first "City, ST" and first parsable date line after the title appears
        # Locate title in lines and scan forward
        start_i = 0
        for i, ln in enumerate(lines):
            if ln.strip().lower() == title.strip().lower():
                start_i = i
                break

        for ln in lines[start_i:start_i + 12]:
            if not location_line:
                if parse_location_flexible(ln):
                    location_line = ln
            if not date_line:
                if parse_us_chess_date_range(ln):
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


def uschess_extract_detail_fields(detail_html: str) -> dict[str, Any]:
    """
    Best-effort extraction from a US Chess event detail page.
    """
    out: dict[str, Any] = {}

    # Try JSON-LD first (often contains address/geo)
    ld = extract_json_ld(detail_html)
    for block in ld:
        # block may be dict or list
        candidates = block if isinstance(block, list) else [block]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            # Look for geo
            geo = obj.get("geo") if isinstance(obj.get("geo"), dict) else None
            if geo and "latitude" in geo and "longitude" in geo:
                try:
                    out["lat"] = float(geo["latitude"])
                    out["lon"] = float(geo["longitude"])
                except Exception:
                    pass
            # Look for location/address
            loc = obj.get("location")
            if isinstance(loc, dict):
                # name/address often in here
                if isinstance(loc.get("name"), str):
                    out.setdefault("venue", loc["name"].strip())
                addr = loc.get("address")
                if isinstance(addr, dict):
                    # sometimes street address present
                    parts = []
                    for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode"):
                        v = addr.get(k)
                        if isinstance(v, str) and v.strip():
                            parts.append(v.strip())
                    if parts:
                        out.setdefault("venue", ", ".join(parts))

    # Also scan raw text for fee / time control / sections
    text = strip_html(detail_html)

    # entry fee
    m = re.search(r"(Entry Fee|Entry fee|ENTRY FEE)\s*[:\-]?\s*([^|•;\n]{1,80})", text)
    if m:
        out["entryFee"] = m.group(2).strip()

    # time control heuristics: pick the first "G/.." pattern
    m = re.search(r"\bG/\s*\d+\s*(?:\+|\s*;\s*|\s*,\s*)\s*\d+\b", text, flags=re.I)
    if m:
        out["timeControl"] = re.sub(r"\s+", "", m.group(0)).replace(",", ";").upper()

    # sections heuristics
    # look for "Sections:" or "Sections include" and capture a bit
    m = re.search(r"\bSections?\b\s*[:\-]\s*([^|•;\n]{1,200})", text, flags=re.I)
    if m:
        raw = m.group(1).strip()
        # split on commas / slashes
        parts = [p.strip() for p in re.split(r"[,/]| and ", raw) if p.strip()]
        out["sections"] = parts[:12]

    # venue/address heuristics
    m = re.search(r"\bLocation\b\s*[:\-]\s*([^|•;\n]{1,120})", text, flags=re.I)
    if m:
        out.setdefault("venue", m.group(1).strip())

    # lat/lon from any map link
    ll = find_lat_lon_from_text(detail_html)
    if ll:
        out.setdefault("lat", ll[0])
        out.setdefault("lon", ll[1])

    return out


def fetch_uschess_all() -> list[dict[str, Any]]:
    source = next(s for s in SOURCE_CATALOG if s["id"] == "uschess-upcoming")
    base = source["homepage"]

    all_items: list[dict[str, str]] = []
    # paginate until we stop seeing new items
    seen_urls: set[str] = set()

    for page in range(0, 80):
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        html_text = fetch_text(url)
        items = uschess_extract_listing_items(html_text, base)

        # Keep only new URLs
        new_items = [it for it in items if it["url"] not in seen_urls]
        for it in new_items:
            seen_urls.add(it["url"])

        print(f"[uschess-upcoming] page={page} items={len(items)} new={len(new_items)}")

        if page > 0 and len(new_items) == 0:
            break

        all_items.extend(new_items)

        # polite pacing
        time.sleep(0.15)

    # Now enrich by fetching detail pages
    events: list[dict[str, Any]] = []
    total = len(all_items)
    for idx, it in enumerate(all_items, start=1):
        if idx % 20 == 0 or idx == total:
            print(f"[uschess-upcoming] enriching {idx}/{total} ...")

        title = it["title"]
        loc = parse_location_flexible(it.get("location_line", "") or "")
        dr = parse_us_chess_date_range(it.get("date_line", "") or "")

        # If listing missed anything, still attempt detail page (sometimes it contains it)
        city = state = ""
        startDate = endDate = ""
        if loc:
            city, state = loc
        if dr:
            startDate, endDate = dr

        try:
            detail_html = fetch_text(it["url"])
        except Exception as e:
            print(f"[uschess-upcoming] detail FAILED {it['url']}: {e}")
            continue

        fields = uschess_extract_detail_fields(detail_html)

        # If listing didn’t capture dates, try again from detail page lines
        if not startDate:
            lines = strip_html_to_lines(detail_html)
            for ln in lines[:200]:
                dr2 = parse_us_chess_date_range(ln)
                if dr2:
                    startDate, endDate = dr2
                    break

        # If listing didn’t capture location, attempt from detail page
        if not city or not state:
            lines = strip_html_to_lines(detail_html)
            for ln in lines[:250]:
                loc2 = parse_location_flexible(ln)
                if loc2:
                    city, state = loc2
                    break

        if not startDate:
            # no parseable date = skip
            continue
        if not city or not state:
            # keep, but mark unknown
            city, state = "Unknown", "US"

        venue = normalize_venue(str(fields.get("venue") or ""), city, state)

        ev = {
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate or startDate,
            "city": city,
            "state": state,
            "venue": venue,
            "lat": fields.get("lat"),
            "lon": fields.get("lon"),
            "entryFee": fields.get("entryFee", ""),
            "timeControl": fields.get("timeControl", ""),
            "sections": fields.get("sections", []) if isinstance(fields.get("sections"), list) else [],
            "sourceId": source["id"],
            "sourceUrl": it["url"],
        }
        events.append(ev)

        time.sleep(0.10)

    return events


# ----------------------------
# Michess: listing -> detail
# ----------------------------
def michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    for path in re.findall(r'(/event-details/[a-z0-9\-]+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)

def infer_year_fallback(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    today = date.today()
    return today.year

def parse_michess_date_range_line(line: str, year: int) -> Optional[tuple[str, str]]:
    s = line.strip()

    # Example: "Fri, Feb 20 - Sun, Feb 22"
    m = re.match(
        r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
        s,
    )
    if m:
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

    # Alternate: "Feb 14, 2026" or "Feb 14, 2026 - Feb 15, 2026"
    m = re.match(r"^([A-Za-z]{3})\s+(\d{1,2}),\s*(20\d{2})(?:\s*-\s*([A-Za-z]{3})\s+(\d{1,2}),\s*(20\d{2}))?$", s)
    if m:
        mon1 = MONTHS_ABBR.get(m.group(1).lower())
        d1 = int(m.group(2))
        y1 = int(m.group(3))
        if not mon1:
            return None
        start = date(y1, mon1, d1)
        if m.group(4):
            mon2 = MONTHS_ABBR.get(m.group(4).lower())
            d2 = int(m.group(5))
            y2 = int(m.group(6))
            if not mon2:
                return None
            end = date(y2, mon2, d2)
        else:
            end = start
        return start.isoformat(), end.isoformat()

    return None

def michess_extract_detail(detail_html: str, url: str, source_id: str) -> Optional[dict[str, Any]]:
    # Title from og:title if present
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    title = html.unescape(m.group(1)).strip() if m else ""
    if not title or title.lower().strip() in {"michigan chess association", "events"}:
        # fallback: first <h1>
        m2 = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
        if m2:
            title = strip_html(m2.group(1)).strip()

    if not title or len(title) < 4:
        return None

    lines = strip_html_to_lines(detail_html)
    year = infer_year_fallback(title + " " + " ".join(lines[:80]))

    startDate = endDate = ""
    for ln in lines[:200]:
        dr = parse_michess_date_range_line(ln, year)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    # location (look for "City, ST")
    city, state = "Unknown", "US"
    venue_line = ""

    for ln in lines[:350]:
        mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if mloc:
            city = mloc.group(1).strip()
            state = mloc.group(2).strip()
            venue_line = ln.strip()
            break

    # Try JSON-LD for geo or address
    fields = {}
    ld = extract_json_ld(detail_html)
    for block in ld:
        candidates = block if isinstance(block, list) else [block]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            geo = obj.get("geo") if isinstance(obj.get("geo"), dict) else None
            if geo and "latitude" in geo and "longitude" in geo:
                try:
                    fields["lat"] = float(geo["latitude"])
                    fields["lon"] = float(geo["longitude"])
                except Exception:
                    pass

    ll = find_lat_lon_from_text(detail_html)
    if ll:
        fields.setdefault("lat", ll[0])
        fields.setdefault("lon", ll[1])

    # Pull label-based fields (best-effort)
    text = " ".join(lines)
    # time control
    tc = ""
    m_tc = re.search(r"(Time Control|Time control)\s*[:\-]\s*([^|•;\n]{1,120})", text)
    if m_tc:
        tc = m_tc.group(2).strip()

    # entry fee
    fee = ""
    m_fee = re.search(r"(Entry Fee|Entry fee)\s*[:\-]\s*([^|•;\n]{1,120})", text)
    if m_fee:
        fee = m_fee.group(2).strip()

    # sections
    sections: list[str] = []
    m_sec = re.search(r"(Sections?|Section)\s*[:\-]\s*([^|•;\n]{1,200})", text, flags=re.I)
    if m_sec:
        raw = m_sec.group(2).strip()
        sections = [p.strip() for p in re.split(r"[,/]| and ", raw) if p.strip()]

    venue = normalize_venue(venue_line, city, state)

    return {
        "id": f"{source_id}-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "venue": venue,
        "lat": fields.get("lat"),
        "lon": fields.get("lon"),
        "entryFee": fee,
        "timeControl": tc,
        "sections": sections[:12],
        "sourceId": source_id,
        "sourceUrl": url,
    }

def fetch_michess_all() -> list[dict[str, Any]]:
    source = next(s for s in SOURCE_CATALOG if s["id"] == "michess")
    listing_html = fetch_text(source["endpoint"])
    urls = michess_extract_detail_urls(listing_html, source["homepage"])

    print(f"[michess] found {len(urls)} event-details urls")
    events: list[dict[str, Any]] = []

    for i, u in enumerate(urls, start=1):
        if i % 10 == 0 or i == len(urls):
            print(f"[michess] fetching {i}/{len(urls)} ...")

        try:
            detail_html = fetch_text(u)
            ev = michess_extract_detail(detail_html, u, source["id"])
            if ev:
                events.append(ev)
        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

        time.sleep(0.10)

    return events


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    all_events: list[dict[str, Any]] = []

    # US Chess
    try:
        us_events = fetch_uschess_all()
        print(f"[uschess-upcoming] fetched {len(us_events)} raw events")
        all_events.extend(us_events)
    except Exception as e:
        print(f"[uschess-upcoming] FAILED: {e}")

    # Michess
    try:
        mi_events = fetch_michess_all()
        print(f"[michess] fetched {len(mi_events)} raw events")
        all_events.extend(mi_events)
    except Exception as e:
        print(f"[michess] FAILED: {e}")

    # upcoming only + dedupe
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
