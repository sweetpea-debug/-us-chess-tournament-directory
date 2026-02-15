#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (TEST MODE).

Sources:
  1) US Chess Upcoming Tournaments: https://new.uschess.org/upcoming-tournaments
  2) Michigan Chess Association: https://www.michess.org/events (and /event-details pages)

Outputs repo-root events.json as:
  { "syncedAt": "<iso>", "events": [ ... ] }

No external dependencies (standard library only).
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
        "homepage": "https://www.michess.org",
        "sitemap": "https://www.michess.org/sitemap.xml",
    },
]


# ----------------------------
# HTTP
# ----------------------------

DEFAULT_TIMEOUT_SECS = 30
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0)"

def fetch_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


# ----------------------------
# US Chess parsing
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

def _clean_title_line(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^#+\s*", "", s)
    s = re.sub(r"^【\d+†\s*", "", s)
    s = s.replace("】", "").strip()
    return s

def _parse_us_chess_date_one(s: str):
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
    try:
        return date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None

def _parse_us_chess_date_range(s: str):
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

def _parse_location_flexible(loc: str):
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
        mid = parts[1]
        last = parts[-1]
        if re.fullmatch(r"[A-Z]{2}", mid):
            return city, mid
        abbr = US_STATE_ABBR.get(last.lower())
        return (city, abbr) if abbr else None

    return None

def _uschess_title_to_url_map(page_html: str, base_url: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for href, inner in re.findall(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S,
    ):
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        out[title.lower()] = urljoin(base_url, href)
    return out

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
        loc = None
        dr = None
        organizer = ""
        desc_lines: list[str] = []

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

        # Grab organizer: first non-empty after the date line
        if dr:
            for j in range(i + 1, min(i + 14, len(lines))):
                if _parse_us_chess_date_range(lines[j]):
                    for k in range(j + 1, min(j + 6, len(lines))):
                        if lines[k].strip() and not lines[k].startswith("###"):
                            organizer = lines[k].strip()
                            break
                    break

        # Description: collect until next title
        k = i + 1
        while k < len(lines) and not lines[k].startswith("###") and "#### Pagination" not in lines[k]:
            desc_lines.append(lines[k])
            k += 1

        if not loc or not dr:
            i += 1
            continue

        city, state = loc
        startDate, endDate = dr

        desc = re.sub(r"\s+", " ", " ".join(desc_lines)).strip()

        entry_fee = ""
        m_fee = re.search(r"Entry fee:\s*([^.;]+)", desc, flags=re.I)
        if m_fee:
            entry_fee = m_fee.group(1).strip()

        time_control = ""
        m_tc = re.search(r"\bG/\s*\d+\s*(?:;|,|\s)\s*d\s*\d+\b", desc, flags=re.I)
        if m_tc:
            time_control = re.sub(r"\s+", "", m_tc.group(0)).replace(",", ";").upper()

        venue = ""
        m_loc = re.search(r"\bLocation:\s*([^.;]+)", desc, flags=re.I)
        if m_loc:
            venue = m_loc.group(1).strip()

        event_url = title_url.get(title.lower(), source["homepage"])

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city.title() if city.isupper() else city,
            "state": state,
            "venue": venue,
            "lat": 39.8283,
            "lon": -98.5795,
            "format": "",
            "entryFee": entry_fee,
            "sections": [],
            "timeControl": time_control,
            "organizer": organizer,
            "description": desc[:800],
            "sourceId": source["id"],
            "sourceUrl": event_url,
        })

        i += 1

    return out


# ----------------------------
# Michess parsing (follow /event-details)
# ----------------------------

MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

def _michess_extract_detail_urls_from_events(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)
    for path in re.findall(r'(/event-details/[a-z0-9\-]+-\d+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))
    return sorted(urls)

def _michess_extract_detail_urls_from_sitemap(sitemap_xml: str) -> list[str]:
    urls: set[str] = set()
    # sitemap has <loc>https://www.michess.org/event-details/....</loc>
    for loc in re.findall(r"<loc>\s*(https?://www\.michess\.org/event-details/[^<\s]+)\s*</loc>", sitemap_xml, flags=re.I):
        urls.add(loc.strip())
    return sorted(urls)

def _infer_year_from_title(title: str) -> int:
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    today = date.today()
    return today.year

def _parse_michess_date_range(line: str, title: str):
    s = line.strip()
    m = re.match(
        r"^[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})\s*-\s*[A-Za-z]{3},\s*([A-Za-z]{3})\s*(\d{1,2})$",
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
    y = _infer_year_from_title(title)

    try:
        start = date(y, mon1, d1)
        end = date(y, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

def _strip_value_after_label(lines: list[str], label: str) -> str:
    label_low = label.lower().strip()
    for i, ln in enumerate(lines):
        if ln.strip().lower() == label_low:
            for j in range(i + 1, min(i + 12, len(lines))):
                v = lines[j].strip()
                if v:
                    if v.endswith(":") and len(v) <= 25:
                        return ""
                    return v
    return ""

def parse_michess_event_detail(detail_html: str, source: dict[str, Any], url: str):
    lines = _strip_html_to_lines(detail_html)

    # Title: first meaningful line
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
        dr = _parse_michess_date_range(ln.strip(), title)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    venue_line = ""
    city = "Unknown"
    state = "US"
    for ln in lines[:400]:
        if "United States" in ln and "," in ln:
            venue_line = ln.strip()
            mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", venue_line)
            if mloc:
                city = mloc.group(1).strip()
                state = mloc.group(2).strip()
            break

    fmt = _strip_value_after_label(lines, "Format:")
    tc = _strip_value_after_label(lines, "Time Control:")
    fee = _strip_value_after_label(lines, "Entry Fee:")

    if tc:
        tc = re.sub(r"\s+", " ", tc).strip()

    return {
        "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "venue": venue_line,
        "lat": 44.3148,
        "lon": -85.6024,
        "format": fmt,
        "entryFee": fee,
        "sections": [],
        "timeControl": tc,
        "sourceId": source["id"],
        "sourceUrl": url,
    }

def parse_michess_events(listing_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    base = source["homepage"]

    urls = _michess_extract_detail_urls_from_events(listing_html, base)
    print(f"[michess] /events contained {len(urls)} event-details urls")

    if not urls:
        # JS-rendered /events sometimes has no links in Actions; use sitemap instead
        try:
            sm = fetch_text(source["sitemap"])
            urls = _michess_extract_detail_urls_from_sitemap(sm)
            print(f"[michess] sitemap contained {len(urls)} event-details urls")
        except Exception as e:
            print(f"[michess] sitemap fetch failed: {e}")
            urls = []

    # Safety cap so Actions doesn't run forever
    urls = urls[:250]

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
        return events

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
