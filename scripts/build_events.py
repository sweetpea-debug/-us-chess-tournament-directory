#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (US Chess + Michigan Chess Association).

Outputs repo-root events.json as:
  { "syncedAt": "<iso>", "events": [ ... ] }

Design goals:
- Standard library only (no external deps).
- Stable runs: reuse prior events.json details when possible to avoid re-fetching everything.
- Better "sourceText": extract main/article content and trim away nav/menus.
- Better michess parsing: prefer JSON-LD (@type Event) when present.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
from dataclasses import dataclass
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
# If scripts/build_events.py, repo root is parent of scripts/
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
        "homepage": "https://www.michess.org/events",
    },
]


# ----------------------------
# HTTP
# ----------------------------

DEFAULT_TIMEOUT_SECS = 30
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.2; +https://github.com/)"
POLITE_SLEEP_SECS = 0.15  # tiny delay between requests

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
    """
    Dedupe by (sourceId + sourceUrl) primarily; fallback to (name + startDate + city + state).
    Prefer first occurrence.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = ""
        if e.get("sourceUrl"):
            key = f"{e.get('sourceId','')}|{e.get('sourceUrl','')}"
        else:
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


def iso_date_only(s: str) -> str:
    """
    Takes '2026-02-16' or '2026-02-16T00:00:00-05:00' and returns '2026-02-16'
    """
    if not s:
        return ""
    return s.split("T", 1)[0].strip()


def parse_date_yyyy_mm_dd(s: str) -> Optional[date]:
    try:
        parts = iso_date_only(s).split("-")
        if len(parts) != 3:
            return None
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None


def _parse_us_chess_date_one(s: str) -> Optional[date]:
    """
    'Wednesday, February 18, 2026' or 'February 18, 2026'
    """
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
    except Exception:
        return None


def _parse_us_chess_date_range(s: str) -> Optional[tuple[str, str]]:
    """
    'Saturday, January 3, 2026 - Sunday, January 4, 2026'
    or 'Wednesday, February 18, 2026'
    """
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
    return start.isoformat(), end.isoformat()


def _parse_location_flexible(loc: str) -> Optional[tuple[str, str]]:
    """
    Accept:
      - 'City, StateName'
      - 'City, ST'
      - 'City, ST, StateName'  (common on US Chess)
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
# HTML → text (preserve newlines better)
# ----------------------------

def html_to_text_lines(markup: str) -> list[str]:
    # Remove scripts/styles
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)
    # Convert list items into lines with dash
    markup = re.sub(r"<li\b[^>]*>", "\n- ", markup, flags=re.I)
    # Force newlines on common block ends / breaks
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)
    markup = re.sub(r"</(p|div|section|article|header|footer|h1|h2|h3|h4|h5|h6|tr|td|th|ul|ol)\s*>", "\n", markup, flags=re.I)
    # Drop remaining tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html_lib.unescape(text)
    # Normalize whitespace, keep line structure
    out: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def extract_html_region(markup: str, patterns: list[tuple[str, str]]) -> str:
    """
    Tries several (start, end) regex pairs; returns the first matched region,
    otherwise returns original markup.
    """
    for start_pat, end_pat in patterns:
        m1 = re.search(start_pat, markup, flags=re.I)
        if not m1:
            continue
        m2 = re.search(end_pat, markup[m1.end():], flags=re.I)
        if not m2:
            continue
        return markup[m1.end(): m1.end() + m2.start()]
    return markup


def trim_text_to_title(lines: list[str], title: str) -> list[str]:
    """
    Drop nav-like content by trimming everything before the first line
    containing the event title (case-insensitive).
    """
    if not title:
        return lines
    t = title.strip().lower()
    for i, ln in enumerate(lines[:300]):
        if t in ln.lower():
            return lines[i:]
    return lines


def drop_obvious_nav_lines(lines: list[str]) -> list[str]:
    """
    Heuristic removal of very common global-site junk.
    """
    junk_exact = {
        "skip to main content",
        "user account menu",
        "login",
        "donate",
        "search",
        "privacy policy",
        "terms of use",
        "contact us",
    }
    junk_contains = [
        "cookie",
        "©",
        "copyright",
        "all rights reserved",
        "facebook",
        "twitter",
        "youtube",
        "rss",
        "site map",
        "subscribe",
        "member site",
    ]
    out: list[str] = []
    for ln in lines:
        low = ln.lower().strip()
        if low in junk_exact:
            continue
        if any(j in low for j in junk_contains):
            continue
        # drop giant menu-looking runs
        if len(ln) > 200 and " | " in ln and ("us chess" in low or "michigan chess" in low):
            continue
        out.append(ln)
    return out


def make_source_text(markup: str, title: str, site_hint: str) -> str:
    """
    Extract main content region then turn into a readable multi-line string.
    site_hint: "uschess" or "michess" (controls region extraction heuristics)
    """
    region_patterns: list[tuple[str, str]] = []
    if site_hint == "uschess":
        # Try <article>, then <main>, then common Drupal node content wrappers.
        region_patterns = [
            (r"<article\b[^>]*>", r"</article>"),
            (r"<main\b[^>]*>", r"</main>"),
            (r'<div[^>]+class="[^"]*(node__content|layout-content|region-content)[^"]*"[^>]*>', r"</div>"),
        ]
    elif site_hint == "michess":
        # Wix-ish pages: try <main> or a central content wrapper.
        region_patterns = [
            (r"<main\b[^>]*>", r"</main>"),
            (r'<div[^>]+id="SITE_CONTAINER"[^>]*>', r"</div>"),
        ]

    region_html = extract_html_region(markup, region_patterns)
    lines = html_to_text_lines(region_html)
    lines = trim_text_to_title(lines, title)
    lines = drop_obvious_nav_lines(lines)

    # Remove duplicated consecutive lines
    deduped: list[str] = []
    prev = ""
    for ln in lines:
        if ln == prev:
            continue
        prev = ln
        deduped.append(ln)

    # Keep it reasonably sized
    MAX_LINES = 220
    if len(deduped) > MAX_LINES:
        deduped = deduped[:MAX_LINES] + ["…"]

    return "\n".join(deduped).strip()


# ----------------------------
# JSON-LD Event parsing (best signal!)
# ----------------------------

def _extract_json_ld_blocks(markup: str) -> list[str]:
    blocks: list[str] = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', markup, flags=re.I | re.S):
        payload = m.group(1).strip()
        if payload:
            blocks.append(payload)
    return blocks


def _walk_find_event(obj: Any) -> Optional[dict[str, Any]]:
    """
    Return the first dict that looks like an Event from a JSON structure.
    """
    if isinstance(obj, dict):
        t = obj.get("@type") or obj.get("type")
        if isinstance(t, str) and t.lower() == "event":
            return obj
        # Some pages embed event under @graph
        for k in ("@graph", "graph"):
            if k in obj:
                found = _walk_find_event(obj[k])
                if found:
                    return found
        # Or embed as "mainEntity"
        for k in ("mainEntity", "main_entity"):
            if k in obj:
                found = _walk_find_event(obj[k])
                if found:
                    return found
        # Otherwise search all values
        for v in obj.values():
            found = _walk_find_event(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _walk_find_event(it)
            if found:
                return found
    return None


def parse_event_from_json_ld(markup: str) -> Optional[dict[str, Any]]:
    """
    Attempts to locate an Event object in JSON-LD scripts.
    Returns a normalized dict with keys: name, startDate, endDate, city, state, venue
    """
    for block in _extract_json_ld_blocks(markup):
        try:
            data = json.loads(block)
        except Exception:
            continue

        ev = _walk_find_event(data)
        if not ev:
            continue

        name = (ev.get("name") or "").strip()

        start_raw = (ev.get("startDate") or "").strip()
        end_raw = (ev.get("endDate") or "").strip()

        start = iso_date_only(start_raw)
        end = iso_date_only(end_raw) if end_raw else start

        # location can be dict or list
        city = ""
        state = ""
        venue = ""

        loc = ev.get("location")
        if isinstance(loc, list) and loc:
            loc = loc[0]

        if isinstance(loc, dict):
            venue = (loc.get("name") or "").strip()
            addr = loc.get("address")
            if isinstance(addr, dict):
                city = (addr.get("addressLocality") or "").strip()
                state = (addr.get("addressRegion") or "").strip()
                street = (addr.get("streetAddress") or "").strip()
                postal = (addr.get("postalCode") or "").strip()
                # If venue missing but address has street, use it as venue-ish
                if not venue:
                    venue = street
                # If venue present but we have a street, append for better clarity
                if venue and street and street.lower() not in venue.lower():
                    venue = f"{venue} — {street}"
                if postal and postal not in venue:
                    # don't overdo it; zip can help but optional
                    pass

        # normalize state names
        if state and len(state) > 2:
            abbr = US_STATE_ABBR.get(state.lower())
            if abbr:
                state = abbr

        if name and start and state:
            return {
                "name": name,
                "startDate": start,
                "endDate": end or start,
                "city": city or "Unknown",
                "state": state,
                "venue": venue or "",
            }

    return None


# ----------------------------
# US Chess Upcoming (listing -> detail URLs)
# ----------------------------

def uschess_extract_event_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    # Common: <h3 ...><a href="/something">TITLE</a></h3>
    for href in re.findall(r"<h3[^>]*>\s*<a[^>]*href=['\"]([^'\"]+)['\"]", listing_html, flags=re.I | re.S):
        if not href:
            continue
        full = urljoin(base_url, href)
        # Heuristic: US Chess event pages are usually on the same domain and not just nav links
        if "new.uschess.org" in full and "/upcoming-tournaments" not in full:
            urls.add(full)

    # Backup: any same-domain link that looks like a node/page and isn't obviously global nav
    for href in re.findall(r'href=["\']([^"\']+)["\']', listing_html, flags=re.I):
        full = urljoin(base_url, href)
        if "new.uschess.org" not in full:
            continue
        if "/upcoming-tournaments" in full:
            continue
        if any(full.endswith(suf) for suf in (".jpg", ".png", ".svg", ".css", ".js")):
            continue
        # Keep pages that are likely content nodes
        if re.search(r"new\.uschess\.org/[a-z0-9\-]+", full):
            urls.add(full)

    return sorted(urls)


def fetch_uschess_upcoming(source: dict[str, Any], prior_by_url: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    all_urls: list[str] = []
    for page in range(0, 80):
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        listing_html = fetch_text(url)
        time.sleep(POLITE_SLEEP_SECS)

        urls = uschess_extract_event_urls(listing_html, source["homepage"])
        # If a page yields nothing, we can stop.
        if not urls and page > 0:
            break

        # Append new URLs only
        before = len(all_urls)
        for u in urls:
            if u not in all_urls:
                all_urls.append(u)

        print(f"[uschess-upcoming] page={page} urls={len(urls)} total={len(all_urls)} (+{len(all_urls)-before})")

        # Safety: if we stop discovering new URLs for a few pages, bail
        if page >= 5 and len(all_urls) == before:
            # no growth on this page
            pass

    print(f"[uschess-upcoming] total unique detail urls: {len(all_urls)}")

    events: list[dict[str, Any]] = []
    fetched = 0
    reused = 0

    for idx, u in enumerate(all_urls, start=1):
        # Reuse prior event if same URL and has at least the basics
        prev = prior_by_url.get(u)
        if prev and prev.get("name") and prev.get("startDate") and prev.get("endDate") and prev.get("sourceText"):
            reused += 1
            events.append(prev)
            continue

        try:
            detail_html = fetch_text(u)
            time.sleep(POLITE_SLEEP_SECS)
            fetched += 1

            ev_ld = parse_event_from_json_ld(detail_html)

            if ev_ld:
                name = ev_ld["name"]
                startDate = ev_ld["startDate"]
                endDate = ev_ld["endDate"]
                city = ev_ld.get("city") or "Unknown"
                state = ev_ld.get("state") or "US"
                venue = ev_ld.get("venue") or ""
            else:
                # Fallback: try to recover from readable text
                lines = html_to_text_lines(detail_html)
                name = lines[0].strip() if lines else u
                city, state = "Unknown", "US"
                startDate = ""
                endDate = ""
                # find location and date line quickly
                for i in range(0, min(220, len(lines))):
                    loc = _parse_location_flexible(lines[i])
                    if loc and city == "Unknown":
                        city, state = loc
                    dr = _parse_us_chess_date_range(lines[i])
                    if dr and not startDate:
                        startDate, endDate = dr
                    if city != "Unknown" and startDate:
                        break
                if not startDate:
                    # skip un-parseable pages
                    continue
                venue = ""

            source_text = make_source_text(detail_html, name, "uschess")

            events.append({
                "id": f"{source['id']}-{sanitize_slug(name)}-{startDate}",
                "name": name,
                "startDate": startDate,
                "endDate": endDate,
                "city": city,
                "state": state,
                "venue": venue,
                "sourceId": source["id"],
                "sourceUrl": u,
                "sourceText": source_text,
            })

            if idx % 100 == 0:
                print(f"[uschess-upcoming] processed {idx}/{len(all_urls)} (fetched={fetched}, reused={reused})")

        except Exception as e:
            print(f"[uschess-upcoming] detail FAILED {u}: {e}")

    print(f"[uschess-upcoming] done (fetched={fetched}, reused={reused})")
    return events


# ----------------------------
# Michess (listing -> /event-details/... -> detail)
# ----------------------------

def michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    # href="/event-details/..."
    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    # href="https://www.michess.org/event-details/..."
    for href in re.findall(r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(href)

    # fallback: raw paths in JSON
    for path in re.findall(r'(/event-details/[a-z0-9\-]+)', listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)


def michess_parse_date_range_from_text(lines: list[str], title: str) -> Optional[tuple[str, str]]:
    """
    Example: 'Fri, Feb 20 - Sun, Feb 22'
    Usually no year; infer from title if present, else current year.
    """
    inferred_year = date.today().year
    m_y = re.search(r"\b(20\d{2})\b", title)
    if m_y:
        inferred_year = int(m_y.group(1))

    for ln in lines[:200]:
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
            start = date(inferred_year, mon1, d1)
            end = date(inferred_year, mon2, d2)
            if end < start:
                end = start
            return start.isoformat(), end.isoformat()
        except Exception:
            continue

    return None


def michess_parse_city_state(lines: list[str]) -> tuple[str, str]:
    """
    Best-effort: find first 'City, ST' where ST is MI preferred.
    """
    for ln in lines[:260]:
        m = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if m:
            city = m.group(1).strip()
            st = m.group(2).strip()
            # prefer MI if present later
            if st == "MI":
                return city, st
    # second pass: any state
    for ln in lines[:260]:
        m = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return "Unknown", "MI"


def fetch_michess(source: dict[str, Any], prior_by_url: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    listing_html = fetch_text(source["endpoint"])
    time.sleep(POLITE_SLEEP_SECS)

    urls = michess_extract_detail_urls(listing_html, source["homepage"])
    print(f"[michess] found {len(urls)} event-details urls")

    events: list[dict[str, Any]] = []
    fetched = 0
    reused = 0

    for idx, u in enumerate(urls, start=1):
        prev = prior_by_url.get(u)
        if prev and prev.get("name") and prev.get("startDate") and prev.get("endDate") and prev.get("sourceText"):
            reused += 1
            events.append(prev)
            continue

        try:
            detail_html = fetch_text(u)
            time.sleep(POLITE_SLEEP_SECS)
            fetched += 1

            ev_ld = parse_event_from_json_ld(detail_html)
            if ev_ld:
                name = ev_ld["name"]
                startDate = ev_ld["startDate"]
                endDate = ev_ld["endDate"]
                city = ev_ld.get("city") or "Unknown"
                state = ev_ld.get("state") or "MI"
                venue = ev_ld.get("venue") or ""
            else:
                # fallback to text parsing inside main-ish content
                region = extract_html_region(detail_html, [
                    (r"<main\b[^>]*>", r"</main>"),
                    (r'<div[^>]+id="SITE_CONTAINER"[^>]*>', r"</div>"),
                ])
                lines = html_to_text_lines(region)

                # Title: prefer first strong-looking line (avoid generic site name)
                name = ""
                for ln in lines[:80]:
                    low = ln.lower().strip()
                    if not ln.strip():
                        continue
                    if low in {"events", "submit event", "michess", "michigan chess association"}:
                        continue
                    if len(ln.strip()) >= 6:
                        name = ln.strip()
                        break
                if not name:
                    name = "Michigan event"

                dr = michess_parse_date_range_from_text(lines, name) or michess_parse_date_range_from_text(html_to_text_lines(detail_html), name)
                if not dr:
                    continue
                startDate, endDate = dr

                city, state = michess_parse_city_state(lines)
                venue = ""

            source_text = make_source_text(detail_html, name, "michess")

            events.append({
                "id": f"{source['id']}-{sanitize_slug(name)}-{startDate}",
                "name": name,
                "startDate": startDate,
                "endDate": endDate,
                "city": city,
                "state": state or "MI",
                "venue": venue,
                "sourceId": source["id"],
                "sourceUrl": u,
                "sourceText": source_text,
            })

            if idx % 20 == 0:
                print(f"[michess] processed {idx}/{len(urls)} (fetched={fetched}, reused={reused})")

        except Exception as e:
            print(f"[michess] detail FAILED {u}: {e}")

    print(f"[michess] done (fetched={fetched}, reused={reused})")
    return events


# ----------------------------
# Prior cache reuse
# ----------------------------

def load_prior_events_by_url() -> dict[str, dict[str, Any]]:
    """
    Load current repo events.json (if present) to reuse per-URL sourceText.
    """
    if not OUTPUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        rows = payload.get("events", [])
        if not isinstance(rows, list):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for e in rows:
            if not isinstance(e, dict):
                continue
            u = e.get("sourceUrl")
            if u:
                out[str(u)] = e
        return out
    except Exception:
        return {}


# ----------------------------
# Orchestrator
# ----------------------------

def fetch_source(source: dict[str, Any], prior_by_url: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    parser = source["parser"]

    if parser == "uschess_upcoming":
        return fetch_uschess_upcoming(source, prior_by_url)

    if parser == "michess_events":
        return fetch_michess(source, prior_by_url)

    return []


def main() -> None:
    prior_by_url = load_prior_events_by_url()
    if prior_by_url:
        print(f"[cache] loaded {len(prior_by_url)} prior events by sourceUrl")

    all_events: list[dict[str, Any]] = []

    for source in SOURCE_CATALOG:
        try:
            events = fetch_source(source, prior_by_url)
            print(f"[{source['id']}] fetched {len(events)} raw events")
            all_events.extend(events)
        except Exception as e:
            print(f"[{source['id']}] FAILED: {e}")

    # Filter out past events
    all_events = [e for e in all_events if is_upcoming(e)]

    # Dedupe
    all_events = dedupe(all_events)

    # Sort by start date
    def _sort_key(e: dict[str, Any]) -> tuple:
        d = parse_date_yyyy_mm_dd(str(e.get("startDate") or "")) or date(2100, 1, 1)
        return (d, str(e.get("state") or ""), str(e.get("city") or ""), str(e.get("name") or ""))

    all_events.sort(key=_sort_key)

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")


if __name__ == "__main__":
    main()
