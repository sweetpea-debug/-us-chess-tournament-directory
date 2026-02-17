#!/usr/bin/env python3
"""
Daily ingest for chess tournaments (streamlined).

Goal:
- Cards show: name, date(s), city/state.
- Detail page shows: cleaned "sourceText" + link to official listing.

Sources:
- US Chess (new.uschess.org/upcoming-tournaments) + event detail pages
- Michigan Chess Association (michess.org/events) + /event-details/... pages

Output:
- repo-root events.json:
  {
    "syncedAt": "<iso>",
    "events": [
      {
        "id": "...",
        "name": "...",
        "startDate": "YYYY-MM-DD",
        "endDate": "YYYY-MM-DD",
        "city": "...",
        "state": "XX",
        "sourceId": "...",
        "sourceUrl": "...",
        "sourceText": "...\n...\n..."
      }
    ]
  }

Performance:
- Listing pages are fast; detail pages can be many.
- We cache sourceText from previous events.json and only refetch missing/new ones.
- We fetch detail pages concurrently.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Config
# ----------------------------

DEFAULT_TIMEOUT_SECS = 25
USER_AGENT = "Mozilla/5.0 (compatible; TournamentRadarBot/1.0)"
MAX_WORKERS = 10

# To keep the first-ever run from taking forever, you can cap how many NEW pages
# get enriched per run. Set to None for no cap.
MAX_NEW_ENRICH = None  # e.g. 300 if you want a hard cap

US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN",
    "MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
    "WA","WV","WI","WY","DC"
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
}

MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "uschess",
        "name": "US Chess",
        "listing": "https://new.uschess.org/upcoming-tournaments",
        "homepage": "https://new.uschess.org/upcoming-tournaments",
    },
    {
        "id": "michess",
        "name": "Michigan Chess Association",
        "listing": "https://www.michess.org/events",
        "homepage": "https://www.michess.org/events",
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
# HTML -> text helpers
# ----------------------------

def _strip_html_to_lines(markup: str) -> list[str]:
    # remove script/style
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)

    # add line breaks for block-ish elements
    markup = re.sub(r"</(p|div|li|h1|h2|h3|h4|h5|tr|td|th|section|article|header|footer|main)\s*>", "\n", markup, flags=re.I)
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    # drop tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = htmllib.unescape(text)

    # normalize lines
    lines: list[str] = []
    for raw in text.splitlines():
        ln = re.sub(r"\s+", " ", raw).strip()
        if ln:
            lines.append(ln)
    return lines

def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"

def is_upcoming(event: dict[str, Any]) -> bool:
    today = date.today().isoformat()
    end_date = str(event.get("endDate") or "")
    return bool(end_date) and end_date >= today

def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('sourceId','')}|{e.get('sourceUrl','')}|{e.get('startDate','')}|{e.get('name','')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out

# ----------------------------
# Date parsing
# ----------------------------

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
    return start.isoformat(), end.isoformat()

def _infer_year_from_title_or_now(title: str) -> int:
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    return date.today().year

def _parse_michess_date_range(line: str, title: str) -> tuple[str, str] | None:
    # Example: "Fri, Feb 20 - Sun, Feb 22"
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
    y = _infer_year_from_title_or_now(title)
    try:
        start = date(y, mon1, d1)
        end = date(y, mon2, d2)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None

# ----------------------------
# Extract title + location (City, ST)
# ----------------------------

def _extract_h1(html: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.I | re.S)
    if not m:
        return ""
    text = re.sub(r"<[^>]+>", " ", m.group(1))
    text = htmllib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _extract_city_state_from_text(text: str) -> tuple[str, str] | None:
    # first reasonable City, ST match
    for m in re.finditer(r"\b([A-Za-z][A-Za-z .'\-]{1,60}?),\s*([A-Z]{2})\b", text):
        city = m.group(1).strip()
        st = m.group(2).strip()
        if st in US_STATES:
            return city, st
    return None

# ----------------------------
# Clean source text (remove nav/menu boilerplate)
# ----------------------------

USCHESS_NOISE = {
    "Skip to main content",
    "US Chess.org",
    "User account menu",
    "Find a Club",
    "Donate",
    "About",
    "Mission and Vision",
    "Governance",
    "Annual Reports",
    "Complaints Procedures",
    "Delegates Information",
    "Documents and Policies",
    "Financials",
    "Executive Board",
    "Staff/Contact Us",
    "FAQs",
    "Job Postings",
    "Helpful Guides",
    "Privacy Policy",
    "Terms of Use",
}

def _extract_main_block(html: str) -> str:
    # Prefer <main>...</main>, else <article>...</article>, else full page.
    m = re.search(r"<main\b[^>]*>(.*?)</main>", html, flags=re.I | re.S)
    if m:
        return m.group(1)
    m = re.search(r"<article\b[^>]*>(.*?)</article>", html, flags=re.I | re.S)
    if m:
        return m.group(1)
    return html

def clean_source_text(html: str, title_hint: str) -> str:
    block = _extract_main_block(html)
    lines = _strip_html_to_lines(block)

    # If title exists in lines, start from it to cut off a lot of header garbage.
    start_idx = 0
    if title_hint:
        low_hint = title_hint.lower()
        for i, ln in enumerate(lines[:250]):
            if low_hint == ln.lower() or low_hint in ln.lower():
                start_idx = i
                break
    lines = lines[start_idx:]

    cleaned: list[str] = []
    for ln in lines:
        if ln in USCHESS_NOISE:
            continue
        if re.fullmatch(r"©\s*\d{4}.*", ln):
            break
        if ln.lower().startswith("copyright"):
            break
        cleaned.append(ln)

    # Deduplicate consecutive identical lines
    out: list[str] = []
    prev = None
    for ln in cleaned:
        if ln == prev:
            continue
        prev = ln
        out.append(ln)

    # Keep it a reasonable size so events.json doesn't explode
    text = "\n".join(out).strip()
    if len(text) > 12000:
        text = text[:12000].rstrip() + "\n…"
    return text

# ----------------------------
# US Chess: listing -> events
# ----------------------------

def parse_uschess_listing(page_html: str, base_url: str) -> list[dict[str, Any]]:
    """
    Extract events from the Upcoming Tournaments listing page.

    We rely on:
    - event links in <a href="..."> inside headings/cards
    - nearby plain-text lines include location and date range
    """
    # Link map by title (best-effort)
    title_to_url: dict[str, str] = {}
    for href, inner in re.findall(r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", page_html, flags=re.I | re.S):
        title = htmllib.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            title_to_url[title.lower()] = urljoin(base_url, href)

    lines = _strip_html_to_lines(page_html)

    events: list[dict[str, Any]] = []
    # Heuristic: blocks look like:
    # ### Title
    # City, ST, StateName
    # Saturday, January 3, 2026 - Sunday, January 4, 2026
    for i in range(0, len(lines) - 3):
        if not lines[i].startswith("###"):
            continue

        title = re.sub(r"^#+\s*", "", lines[i]).strip()
        if len(title) < 4:
            continue

        loc_line = lines[i + 1].strip()
        date_line = lines[i + 2].strip()

        dr = _parse_us_chess_date_range(date_line)
        if not dr:
            continue

        # location can be "City, ST, StateName" or "City, ST"
        loc = _extract_city_state_from_text(loc_line)
        if not loc:
            continue
        city, st = loc

        startDate, endDate = dr
        url = title_to_url.get(title.lower(), base_url)

        events.append({
            "id": f"uschess-{sanitize_slug(title)}-{startDate}",
            "name": title,
            "startDate": startDate,
            "endDate": endDate,
            "city": city,
            "state": st,
            "sourceId": "uschess",
            "sourceUrl": url,
            "sourceText": "",  # filled in later
        })

    return events

def fetch_uschess_all() -> list[dict[str, Any]]:
    base = "https://new.uschess.org/upcoming-tournaments"
    all_events: list[dict[str, Any]] = []
    for page in range(0, 80):
        url = base if page == 0 else f"{base}?page={page}"
        html = fetch_text(url)
        chunk = parse_uschess_listing(html, base)
        print(f"[uschess] page={page} parsed={len(chunk)}")
        if not chunk and page > 0:
            break
        all_events.extend(chunk)
    return all_events

# ----------------------------
# Michess: listing -> detail urls -> events
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

def parse_michess_detail(detail_html: str, url: str) -> dict[str, Any] | None:
    title = _extract_h1(detail_html)
    if not title:
        # fallback: first meaningful line in main block
        lines = _strip_html_to_lines(_extract_main_block(detail_html))
        title = lines[0] if lines else ""
    if not title:
        return None

    block = _extract_main_block(detail_html)
    lines = _strip_html_to_lines(block)

    # date range
    startDate = endDate = None
    for ln in lines[:200]:
        dr = _parse_michess_date_range(ln, title)
        if dr:
            startDate, endDate = dr
            break

    if not startDate:
        # some michess pages have single date with year; try a fallback
        # Example: "Feb 17, 2026"
        for ln in lines[:200]:
            m = re.search(r"\b([A-Za-z]{3})\s+(\d{1,2}),\s*(20\d{2})\b", ln)
            if m:
                mon = MONTHS_ABBR.get(m.group(1).lower())
                if mon:
                    try:
                        d = date(int(m.group(3)), mon, int(m.group(2)))
                        startDate = d.isoformat()
                        endDate = startDate
                        break
                    except ValueError:
                        pass
    if not startDate:
        return None

    # city/state
    city = "Unknown"
    st = "MI"
    for ln in lines[:250]:
        loc = _extract_city_state_from_text(ln)
        if loc:
            city, st = loc
            break

    source_text = clean_source_text(detail_html, title)

    return {
        "id": f"michess-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": st,
        "sourceId": "michess",
        "sourceUrl": url,
        "sourceText": source_text,
    }

def fetch_michess_all() -> list[dict[str, Any]]:
    listing_url = "https://www.michess.org/events"
    listing_html = fetch_text(listing_url)
    urls = michess_extract_detail_urls(listing_html, listing_url)
    print(f"[michess] found {len(urls)} event-details urls")

    out: list[dict[str, Any]] = []
    for u in urls:
        try:
            html = fetch_text(u)
            ev = parse_michess_detail(html, u)
            if ev:
                out.append(ev)
        except Exception as e:
            print(f"[michess] detail failed {u}: {e}")
    return out

# ----------------------------
# Enrichment (US Chess detail pages)
# ----------------------------

def enrich_event_source_text(event: dict[str, Any]) -> dict[str, Any]:
    url = event["sourceUrl"]
    html = fetch_text(url)

    # Use H1 as best title hint, else event.name
    title_hint = _extract_h1(html) or event.get("name", "")
    event["sourceText"] = clean_source_text(html, title_hint)
    return event

def load_previous_cache() -> dict[str, str]:
    """
    Map sourceUrl -> sourceText from existing events.json.
    """
    if not OUTPUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        events = payload.get("events", [])
        cache: dict[str, str] = {}
        for e in events:
            url = e.get("sourceUrl")
            txt = e.get("sourceText")
            if isinstance(url, str) and isinstance(txt, str) and txt.strip():
                cache[url] = txt
        return cache
    except Exception:
        return {}

# ----------------------------
# Main
# ----------------------------

def main() -> None:
    prev_cache = load_previous_cache()
    if prev_cache:
        print(f"[cache] loaded {len(prev_cache)} cached sourceText entries from existing events.json")

    # Fetch listings
    us_events = fetch_uschess_all()
    mi_events = fetch_michess_all()

    # Filter upcoming + dedupe early
    all_events = [e for e in (us_events + mi_events) if is_upcoming(e)]
    all_events = dedupe(all_events)

    # Apply cached sourceText where available
    new_to_enrich: list[dict[str, Any]] = []
    for e in all_events:
        url = e.get("sourceUrl", "")
        if url in prev_cache and prev_cache[url].strip():
            e["sourceText"] = prev_cache[url]
        else:
            # Only US Chess events need enrichment (michess already includes sourceText from detail page)
            if e.get("sourceId") == "uschess":
                new_to_enrich.append(e)

    if MAX_NEW_ENRICH is not None:
        new_to_enrich = new_to_enrich[:MAX_NEW_ENRICH]

    print(f"[uschess] total upcoming={sum(1 for e in all_events if e.get('sourceId')=='uschess')}")
    print(f"[uschess] need enrichment={len(new_to_enrich)}")

    # Enrich US Chess in parallel
    if new_to_enrich:
        enriched: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(enrich_event_source_text, e): e for e in new_to_enrich}
            done = 0
            total = len(futures)
            for fut in as_completed(futures):
                done += 1
                if done % 25 == 0 or done == total:
                    print(f"[uschess] enriching {done}/{total} ...")
                try:
                    enriched.append(fut.result())
                except Exception as err:
                    e = futures[fut]
                    print(f"[uschess] enrich FAILED url={e.get('sourceUrl')}: {err}")

    # Final sort by startDate
    all_events.sort(key=lambda e: e.get("startDate", "9999-12-31"))

    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(all_events)} events")

if __name__ == "__main__":
    main()
