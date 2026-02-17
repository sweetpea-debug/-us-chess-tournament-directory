#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (lightweight, standard library only).

Sources (for now):
  1) US Chess Upcoming Tournaments: https://new.uschess.org/upcoming-tournaments
  2) Michigan Chess Association events: https://www.michess.org/events (follows /event-details/ pages)

Output:
  repo-root events.json:
    { "syncedAt": "<iso UTC>", "events": [ { id,name,startDate,endDate,city,state,sourceUrl,sourceId,sourceText } ] }

Design choice (streamlined UI):
- Cards show only name, date(s), city/state.
- Details page shows sourceText (cleaned) + source link.
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
# Utilities
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
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"

def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('name','')}|{e.get('startDate','')}|{e.get('city','')}|{e.get('state','')}|{e.get('sourceUrl','')}"
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
# HTML -> readable text
# ----------------------------

def _extract_main_html(page_html: str) -> str:
    """
    Best-effort: prefer <main>...</main>. If missing, fall back to <body>.
    Then remove obvious nav/header/footer/aside blocks.
    """
    lower = page_html.lower()

    def pick(tag: str) -> str | None:
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", page_html, flags=re.I | re.S)
        return m.group(1) if m else None

    chunk = pick("main") or pick("article") or pick("body") or page_html

    # remove common wrappers inside the chunk
    chunk = re.sub(r"<(header|nav|footer|aside)\b[^>]*>.*?</\1>", " ", chunk, flags=re.I | re.S)
    # remove scripts/styles
    chunk = re.sub(r"<script\b[^>]*>.*?</script>", " ", chunk, flags=re.I | re.S)
    chunk = re.sub(r"<style\b[^>]*>.*?</style>", " ", chunk, flags=re.I | re.S)

    return chunk

def _html_to_lines(markup: str) -> list[str]:
    """
    Convert HTML to cleaned lines with reasonable line breaks.
    """
    markup = markup.replace("\r", "\n")

    # block-ish tags -> newline
    markup = re.sub(r"</(p|div|li|h1|h2|h3|h4|h5|h6|tr|td|th|section|article|ul|ol)\s*>", "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    # links: keep visible text, keep URL if useful
    # (We just drop tags; text remains.)
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    raw_lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in raw_lines if ln]

    return lines

def _clean_lines_generic(lines: list[str]) -> list[str]:
    """
    Remove obvious site-wide junk lines.
    """
    junk_exact = {
        "skip to main content",
        "user account menu",
        "open main menu",
        "close menu",
        "search",
        "login",
        "log in",
    }

    cleaned: list[str] = []
    for ln in lines:
        low = ln.lower().strip()

        if low in junk_exact:
            continue
        # kill pure “menu-ish” single words that show up a lot
        if len(low) <= 2:
            continue
        # drop very repetitive nav-ish separators
        if low in {"|", "•"}:
            continue

        cleaned.append(ln)

    return cleaned

def extract_source_text(page_html: str) -> str:
    main_html = _extract_main_html(page_html)
    lines = _html_to_lines(main_html)
    lines = _clean_lines_generic(lines)

    # collapse insane repeats
    out: list[str] = []
    prev = ""
    repeat = 0
    for ln in lines:
        if ln == prev:
            repeat += 1
            if repeat >= 2:
                continue
        else:
            repeat = 0
        prev = ln
        out.append(ln)

    # keep it from exploding the JSON
    text = "\n".join(out).strip()
    return text[:12000]


# ----------------------------
# Metadata extraction (title, dates, location)
# ----------------------------

def extract_title(page_html: str) -> str:
    # 1) og:title
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', page_html, flags=re.I)
    if m:
        t = html.unescape(m.group(1)).strip()
        t = re.sub(r"\s+", " ", t)
        return t

    # 2) <title>
    m = re.search(r"<title[^>]*>(.*?)</title>", page_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        t = re.sub(r"\s+", " ", t)
        # strip common suffixes
        t = re.sub(r"\s*\|\s*US Chess\.org\s*$", "", t, flags=re.I)
        t = re.sub(r"\s*\|\s*Michigan Chess Association\s*$", "", t, flags=re.I)
        return t

    # 3) first h1
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, flags=re.I | re.S)
    if m:
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        t = re.sub(r"\s+", " ", t)
        return t

    return "Untitled event"

def _parse_us_chess_date_one(s: str) -> date | None:
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

def parse_date_range_from_lines(lines: list[str]) -> tuple[str, str] | None:
    """
    Try multiple formats:
      - Saturday, January 3, 2026 - Sunday, January 4, 2026
      - January 3, 2026 - January 4, 2026
      - Feb 16, 2026
      - Tue, Feb 17 - Tue, Feb 17
      - Feb 19, 2026 – Feb 21, 2026
    """
    for ln in lines[:250]:
        s = ln.strip()

        # Normalize dash
        s = s.replace("–", "-").replace("—", "-")

        # A) Full month name formats
        if " - " in s and re.search(r"\b20\d{2}\b", s):
            parts = [p.strip() for p in s.split(" - ")]
            if parts:
                start = _parse_us_chess_date_one(parts[0])
                end = _parse_us_chess_date_one(parts[1]) if len(parts) > 1 else start
                if start and end:
                    return start.isoformat(), end.isoformat()

        # B) Abbrev month formats with year: "Feb 16, 2026" or "Feb 16 2026"
        m = re.search(r"\b([A-Za-z]{3,4})\s+(\d{1,2}),?\s*(20\d{2})\b", s)
        if m:
            mon = MONTHS_ABBR.get(m.group(1).lower()[:3])
            if mon:
                d = int(m.group(2)); y = int(m.group(3))
                try:
                    dt = date(y, mon, d)
                    return dt.isoformat(), dt.isoformat()
                except ValueError:
                    pass

        # C) Abbrev range without year but with year elsewhere on line:
        # "Tue, Feb 17 - Tue, Feb 17 6:00pm - 9:30pm" (infer year as current or next)
        m2 = re.search(r"\b([A-Za-z]{3})\s+(\d{1,2})\s*-\s*[A-Za-z]{3},?\s*([A-Za-z]{3})\s+(\d{1,2})\b", s)
        if m2:
            mon1 = MONTHS_ABBR.get(m2.group(1).lower())
            mon2 = MONTHS_ABBR.get(m2.group(3).lower())
            if mon1 and mon2:
                d1 = int(m2.group(2)); d2 = int(m2.group(4))
                y = date.today().year
                # if we're late in year and event looks early, allow next year
                if mon1 <= 3 and date.today().month >= 10:
                    y += 1
                try:
                    start = date(y, mon1, d1)
                    end = date(y, mon2, d2)
                    if end < start:
                        end = start
                    return start.isoformat(), end.isoformat()
                except ValueError:
                    pass

    return None

def extract_city_state_from_text(text: str) -> tuple[str, str] | None:
    """
    Find first "City, ST" in the cleaned main text.
    """
    m = re.search(r"\b([A-Za-z][A-Za-z .'\-]+),\s*([A-Z]{2})\b", text)
    if m:
        city = m.group(1).strip()
        st = m.group(2).strip()
        return city, st
    return None


# ----------------------------
# US Chess upcoming listing
# ----------------------------

def uschess_extract_event_links(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()
    # links on upcoming page: /some-slug
    for href in re.findall(r'href=["\'](/[^"\']+)["\']', listing_html, flags=re.I):
        if href.startswith("/upcoming-tournaments"):
            continue
        if href.startswith("/wp-"):
            continue
        # keep only plausible event pages (avoid assets)
        if any(href.lower().endswith(ext) for ext in [".jpg", ".png", ".css", ".js", ".svg", ".pdf"]):
            continue
        # US Chess event pages are typically on the same domain; still, keep it simple:
        if href.count("/") < 1:
            continue
        full = urljoin(base_url, href)
        if "new.uschess.org" in full:
            urls.add(full)

    # Also capture absolute links
    for href in re.findall(r'href=["\'](https?://new\.uschess\.org/[^"\']+)["\']', listing_html, flags=re.I):
        if any(href.lower().endswith(ext) for ext in [".jpg", ".png", ".css", ".js", ".svg", ".pdf"]):
            continue
        urls.add(href)

    return sorted(urls)

def parse_uschess_upcoming() -> list[dict[str, Any]]:
    source = next(s for s in SOURCE_CATALOG if s["id"] == "uschess-upcoming")
    events: list[dict[str, Any]] = []

    # paginate a bit
    all_links: set[str] = set()
    for page in range(0, 15):
        url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
        html_text = fetch_text(url)
        links = uschess_extract_event_links(html_text, source["homepage"])
        if page == 0:
            pass
        if not links and page > 0:
            break
        for u in links:
            all_links.add(u)

    links_sorted = sorted(all_links)
    print(f"[uschess-upcoming] found {len(links_sorted)} candidate event links")

    # fetch and parse each event page (this is the expensive step)
    for idx, u in enumerate(links_sorted, start=1):
        if idx % 50 == 0 or idx == 1 or idx == len(links_sorted):
            print(f"[uschess-upcoming] parsing {idx}/{len(links_sorted)} ...")

        try:
            detail_html = fetch_text(u)
        except Exception as e:
            print(f"[uschess-upcoming] FAILED fetch {u}: {e}")
            continue

        title = extract_title(detail_html)
        source_text = extract_source_text(detail_html)

        # dates
        lines = source_text.splitlines()
        dr = parse_date_range_from_lines(lines)
        if not dr:
            # if we cannot find a date, skip (prevents junk pages)
            continue
        startDate, endDate = dr

        # city/state
        loc = extract_city_state_from_text(source_text)
        if not loc:
            city, st = "Unknown", "US"
        else:
            city, st = loc

        events.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": st,
            "sourceId": source["id"],
            "sourceUrl": u,
            "sourceText": source_text,
        })

    return events


# ----------------------------
# Michess events (/event-details/)
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

def parse_michess_events() -> list[dict[str, Any]]:
    source = next(s for s in SOURCE_CATALOG if s["id"] == "michess")

    listing_html = fetch_text(source["endpoint"])
    urls = michess_extract_detail_urls(listing_html, source["homepage"])
    print(f"[michess] found {len(urls)} event-details urls")

    out: list[dict[str, Any]] = []
    for idx, u in enumerate(urls, start=1):
        if idx % 20 == 0 or idx == 1 or idx == len(urls):
            print(f"[michess] parsing {idx}/{len(urls)} ...")

        try:
            detail_html = fetch_text(u)
        except Exception as e:
            print(f"[michess] FAILED fetch {u}: {e}")
            continue

        title = extract_title(detail_html)
        source_text = extract_source_text(detail_html)
        lines = source_text.splitlines()

        dr = parse_date_range_from_lines(lines)
        if not dr:
            continue
        startDate, endDate = dr

        loc = extract_city_state_from_text(source_text)
        if not loc:
            city, st = "Unknown", "MI"
        else:
            city, st = loc

        out.append({
            "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": st,
            "sourceId": source["id"],
            "sourceUrl": u,
            "sourceText": source_text,
        })

    return out


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    all_events: list[dict[str, Any]] = []

    try:
        us_events = parse_uschess_upcoming()
        print(f"[uschess-upcoming] fetched {len(us_events)} raw events")
        all_events.extend(us_events)
    except Exception as e:
        print(f"[uschess-upcoming] FAILED: {e}")

    try:
        mi_events = parse_michess_events()
        print(f"[michess] fetched {len(mi_events)} raw events")
        all_events.extend(mi_events)
    except Exception as e:
        print(f"[michess] FAILED: {e}")

    # Filter out past events
    all_events = [e for e in all_events if is_upcoming(e)]

    # Dedupe
    all_events = dedupe(all_events)

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")


if __name__ == "__main__":
    main()
