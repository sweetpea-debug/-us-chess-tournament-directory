#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (TEST MODE).

Sources:
  1) US Chess Upcoming Tournaments: https://new.uschess.org/upcoming-tournaments
  2) Michigan Chess Association: https://www.michess.org/events (and /event-details pages)

Outputs repo-root events.json as:
  { "syncedAt": "<iso>", "events": [ ... ] }

Standard library only (no external deps).
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
# Generic helpers
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


def _grab_value_after_label(lines: list[str], label: str) -> str:
    """
    Handles both 'Label:' and 'Label' forms, with the value on the next line.
    """
    want = label.lower().strip().rstrip(":")
    for i, ln in enumerate(lines):
        cur = ln.strip().lower().rstrip(":")
        if cur == want:
            for j in range(i + 1, min(i + 20, len(lines))):
                v = lines[j].strip()
                if not v:
                    continue
                # stop if next label
                if v.endswith(":") and len(v) <= 35:
                    return ""
                return v
    return ""


def _parse_sections(text: str) -> list[str]:
    if not text:
        return []
    # split on commas/semicolons/bullets
    parts = re.split(r"\s*[;,•]\s*|\s+\|\s+", text)
    parts = [p.strip() for p in parts if p.strip()]
    # don't return insane stuff
    out: list[str] = []
    for p in parts:
        if len(p) > 80:
            continue
        out.append(p)
    return out


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

def _parse_us_chess_date_one(s: str):
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

def _uschess_blocks(page_html: str, base_url: str) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    matches = list(re.finditer(
        r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h3>",
        page_html,
        flags=re.I | re.S,
    ))
    for idx, m in enumerate(matches):
        href = m.group(1)
        inner = m.group(2)
        title = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        event_url = urljoin(base_url, href)

        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(page_html), start + 25000)
        snippet = page_html[start:end]
        blocks.append((title, event_url, snippet))
    return blocks

def parse_uschess_upcoming(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    blocks = _uschess_blocks(page_html, source["homepage"])
    for title, event_url, snippet_html in blocks:
        lines = _strip_html_to_lines(snippet_html)

        loc = None
        dr = None
        organizer = ""

        for ln in lines[:40]:
            loc_try = _parse_location_flexible(ln)
            if loc_try:
                loc = loc_try
                break

        for ln in lines[:80]:
            dr_try = _parse_us_chess_date_range(ln)
            if dr_try:
                dr = dr_try
                break

        if dr:
            for i, ln in enumerate(lines[:120]):
                if _parse_us_chess_date_range(ln):
                    for j in range(i + 1, min(i + 10, len(lines))):
                        if lines[j].strip():
                            organizer = lines[j].strip()
                            break
                    break

        if not loc or not dr:
            continue

        city, state = loc
        startDate, endDate = dr

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city.title() if city.isupper() else city,
            "state": state,
            "venue": "",
            "lat": 39.8283,
            "lon": -98.5795,
            "entryFee": "",
            "sections": [],
            "timeControl": "",
            "organizer": organizer,
            "description": "",
            "sourceId": source["id"],
            "sourceUrl": event_url,
        })
    return out

def enrich_uschess_event(event: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch event detail page and parse key fields reliably from labels.
    """
    url = event.get("sourceUrl", "")
    if not url:
        return event

    try:
        detail_html = fetch_text(url)
    except Exception:
        return event

    lines = _strip_html_to_lines(detail_html)

    # Venue/location: look for a "Location" label and grab next line; fallback to addressy line with City, ST
    venue = _grab_value_after_label(lines, "Location:")
    if not venue:
        venue = _grab_value_after_label(lines, "Location")
    if not venue:
        for ln in lines[:600]:
            if re.search(r"\b[A-Za-z .'-]+,\s*[A-Z]{2}\b", ln) and re.search(r"\d{2,}|\b\d{5}\b|\bUnited States\b", ln):
                venue = ln.strip()
                break

    time_control = _grab_value_after_label(lines, "Time Control:")
    if not time_control:
        time_control = _grab_value_after_label(lines, "Time Control")

    entry_fee = _grab_value_after_label(lines, "Entry Fee:")
    if not entry_fee:
        entry_fee = _grab_value_after_label(lines, "Entry Fee")
    if not entry_fee:
        entry_fee = _grab_value_after_label(lines, "Entry fee:")
    if not entry_fee:
        entry_fee = _grab_value_after_label(lines, "Entry fee")

    sections_raw = _grab_value_after_label(lines, "Sections:")
    if not sections_raw:
        sections_raw = _grab_value_after_label(lines, "Sections")

    # Description (optional)
    description = ""
    # grab first ~250 lines into a readable blob, but keep it short
    description = re.sub(r"\s+", " ", " ".join(lines[:250])).strip()[:900]

    event["venue"] = venue or event.get("venue", "")
    event["timeControl"] = time_control or event.get("timeControl", "")
    event["entryFee"] = entry_fee or event.get("entryFee", "")
    event["sections"] = _parse_sections(sections_raw) or event.get("sections", [])
    event["description"] = description or event.get("description", "")

    return event


# ----------------------------
# Michess parsing
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
    for loc in re.findall(r"<loc>\s*(https?://www\.michess\.org/event-details/[^<\s]+)\s*</loc>", sitemap_xml, flags=re.I):
        urls.add(loc.strip())
    return sorted(urls)

def _infer_year_from_text(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    return date.today().year

def _parse_michess_date_range(line: str, year_hint_text: str):
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
    y = _infer_year_from_text(year_hint_text)

    try:
        start = date(y, mon1, d1)
        end = date(y, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

def _extract_meta_title(detail_html: str) -> str:
    m = re.search(r'property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', detail_html, flags=re.I)
    if m:
        return html.unescape(m.group(1)).strip()

    m2 = re.search(r"<title>\s*(.*?)\s*</title>", detail_html, flags=re.I | re.S)
    if m2:
        t = re.sub(r"<[^>]+>", " ", m2.group(1))
        t = html.unescape(re.sub(r"\s+", " ", t)).strip()
        t = re.split(r"\s+\|\s+", t)[0].strip()
        return t

    m3 = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", detail_html, flags=re.I | re.S)
    if m3:
        t = re.sub(r"<[^>]+>", " ", m3.group(1))
        return html.unescape(re.sub(r"\s+", " ", t)).strip()

    return ""

def parse_michess_event_detail(detail_html: str, source: dict[str, Any], url: str):
    title = _extract_meta_title(detail_html)
    if not title or title.lower() in {"michigan chess association", "events"}:
        return None

    lines = _strip_html_to_lines(detail_html)

    startDate = endDate = None
    year_hint = " ".join([title] + lines[:100])
    for ln in lines[:250]:
        dr = _parse_michess_date_range(ln, year_hint)
        if dr:
            startDate, endDate = dr
            break
    if not startDate:
        return None

    venue_line = ""
    city = "Unknown"
    state = "US"

    for ln in lines[:600]:
        mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if not mloc:
            continue
        looks_addressy = bool(re.search(r"\d{2,}|\bUnited States\b|\b\d{5}\b", ln))
        if looks_addressy or not venue_line:
            venue_line = ln.strip()
            city = mloc.group(1).strip()
            state = mloc.group(2).strip()
            if looks_addressy:
                break

    fmt = _grab_value_after_label(lines, "Format:")
    if not fmt:
        fmt = _grab_value_after_label(lines, "Format")

    tc = _grab_value_after_label(lines, "Time Control:")
    if not tc:
        tc = _grab_value_after_label(lines, "Time Control")

    fee = _grab_value_after_label(lines, "Entry Fee:")
    if not fee:
        fee = _grab_value_after_label(lines, "Entry Fee")

    sections_raw = _grab_value_after_label(lines, "Sections:")
    if not sections_raw:
        sections_raw = _grab_value_after_label(lines, "Sections")

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
        "sections": _parse_sections(sections_raw),
        "timeControl": re.sub(r"\s+", " ", tc).strip() if tc else "",
        "sourceId": source["id"],
        "sourceUrl": url,
    }

def parse_michess_events(listing_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    base = source["homepage"]

    urls = _michess_extract_detail_urls_from_events(listing_html, base)
    print(f"[michess] /events contained {len(urls)} event-details urls")

    if not urls:
        try:
            sm = fetch_text(source["sitemap"])
            urls = _michess_extract_detail_urls_from_sitemap(sm)
            print(f"[michess] sitemap contained {len(urls)} event-details urls")
        except Exception as e:
            print(f"[michess] sitemap fetch failed: {e}")
            urls = []

    urls = urls[:250]  # safety cap

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
        for page in range(0, 120):
            url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
            html_text = fetch_text(url)
            page_events = parse_uschess_upcoming(html_text, source)
            print(f"[uschess-upcoming] page={page} parsed={len(page_events)}")
            if not page_events and page > 0:
                break
            events.extend(page_events)

        # Deduplicate by sourceUrl before enrichment to avoid double-fetches
        by_url: dict[str, dict[str, Any]] = {}
        for e in events:
            by_url[e["sourceUrl"]] = e

        unique = list(by_url.values())
        print(f"[uschess-upcoming] unique events before enrichment: {len(unique)}")

        # Enrich a reasonable cap (prevents Actions from running forever)
        cap = 200
        enriched: list[dict[str, Any]] = []
        for idx, e in enumerate(unique[:cap], start=1):
            if idx % 20 == 0:
                print(f"[uschess-upcoming] enriching {idx}/{min(cap, len(unique))} ...")
            enriched.append(enrich_uschess_event(e))

        # If there were more than cap, include the rest un-enriched (still useful)
        if len(unique) > cap:
            enriched.extend(unique[cap:])

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
