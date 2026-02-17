#!/usr/bin/env python3
"""Daily ingest for chess tournaments.

What this does:
- Pulls tournament listings from:
  1) US Chess "Upcoming Tournaments" (new.uschess.org/upcoming-tournaments)
  2) Michigan Chess Association events (michess.org/events)
- Writes repo-root events.json as:
    { "syncedAt": "<iso>", "events": [ ... ] }

Design choice (streamlined UI):
- Cards show: name, date(s), city/state.
- Detail page shows: full captured source text + a link to the source.

Implementation notes:
- Standard library only.
- US Chess has many events; we fetch listing pages, then fetch each event page in parallel to capture
  the event-specific text (trying to avoid global menus/headers).
"""

from __future__ import annotations

import html
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

DEFAULT_TIMEOUT_SECS = 35
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
# HTML -> text helpers
# ----------------------------


def _remove_scripts_styles(markup: str) -> str:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)
    return markup


def _extract_tag_block(markup: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", markup, flags=re.I | re.S)
    return m.group(1) if m else None


def _extract_best_main_block(markup: str) -> str:
    """Try to keep only event-specific content.

    Heuristic:
    1) Prefer <main>...</main>
    2) Else prefer <article>...</article>
    3) Else return <body>...</body>
    """

    markup = _remove_scripts_styles(markup)

    for tag in ("main", "article"):
        block = _extract_tag_block(markup, tag)
        if block and len(block) > 500:
            # Remove obvious chrome that can still sit inside <main> on some sites.
            block = re.sub(r"<nav\b[^>]*>.*?</nav>", " ", block, flags=re.I | re.S)
            block = re.sub(r"<header\b[^>]*>.*?</header>", " ", block, flags=re.I | re.S)
            block = re.sub(r"<footer\b[^>]*>.*?</footer>", " ", block, flags=re.I | re.S)
            return block

    body = _extract_tag_block(markup, "body")
    return body if body else markup


def html_to_text_preserve_newlines(markup: str) -> str:
    """Convert HTML to readable text with line breaks."""

    markup = _remove_scripts_styles(markup)

    # Remove SVGs (often navigation icons)
    markup = re.sub(r"<svg\b[^>]*>.*?</svg>", " ", markup, flags=re.I | re.S)

    # Convert certain block tags to newlines.
    # (Order matters: add newlines before stripping tags.)
    markup = re.sub(
        r"</(p|div|li|h1|h2|h3|h4|h5|h6|tr|td|th|section|article|header|footer|address|blockquote)\s*>",
        "\n",
        markup,
        flags=re.I,
    )
    markup = re.sub(r"<br\s*/?>", "\n", markup, flags=re.I)

    # Lists: put a dash before list items.
    markup = re.sub(r"<li\b[^>]*>", "- ", markup, flags=re.I)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text)

    # Normalize whitespace but keep newlines.
    text = text.replace("\r", "")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Trim each line
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    return "\n".join(lines)


def _strip_html_to_lines(markup: str) -> list[str]:
    """Lightweight text line extraction used for list pages."""
    markup = _remove_scripts_styles(markup)

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


# ----------------------------
# Generic helpers
# ----------------------------


def sanitize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] if value else "event"


def dedupe(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('name','')}|{e.get('startDate','')}|{e.get('city','')}|{e.get('state','')}|{e.get('sourceId','')}"
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
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


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
    """Accept:
    - City, ST
    - City, StateName
    - City, ST, StateName (common)
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


def parse_uschess_upcoming(page_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _strip_html_to_lines(page_html)
    title_url = _uschess_title_to_url_map(page_html, source["homepage"])

    out: list[dict[str, Any]] = []

    # The listing page often appears as repeating blocks:
    #   Title
    #   City, ST, StateName
    #   Date range
    # We'll scan for that pattern.
    for i in range(0, max(0, len(lines) - 3)):
        title = lines[i].strip()
        loc = lines[i + 1].strip()
        date_line = lines[i + 2].strip()

        if len(title) < 6:
            continue
        if title.lower() in {"upcoming tournaments", "tournaments", "events"}:
            continue

        loc_parsed = _parse_location_flexible(loc)
        dr = _parse_us_chess_date_range(date_line)
        if not loc_parsed or not dr:
            continue

        city, state = loc_parsed
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
                "sourceId": source["id"],
                "sourceUrl": event_url,
                "sourceText": "",  # filled during enrichment
            }
        )

    return out


def enrich_event_source_text(event: dict[str, Any]) -> dict[str, Any]:
    url = str(event.get("sourceUrl") or "")
    if not url:
        return event

    try:
        raw = fetch_text(url)
        main_block = _extract_best_main_block(raw)
        text = html_to_text_preserve_newlines(main_block)

        # Keep payload size reasonable
        event["sourceText"] = text[:20000]
    except Exception as e:
        event["sourceText"] = f"(Could not fetch source text: {e})"

    return event


# ----------------------------
# Michess parsing
# ----------------------------

MONTHS_ABBR = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _michess_extract_detail_urls(listing_html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    for href in re.findall(r'href=["\'](/event-details/[^"\']+)["\']', listing_html, flags=re.I):
        urls.add(urljoin(base_url, href))

    for href in re.findall(
        r'href=["\'](https?://www\.michess\.org/event-details/[^"\']+)["\']',
        listing_html,
        flags=re.I,
    ):
        urls.add(href)

    for path in re.findall(r"(/event-details/[a-z0-9\-]+-\d+)", listing_html, flags=re.I):
        urls.add(urljoin(base_url, path))

    return sorted(urls)


def _infer_year_from_title(title: str) -> int:
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    return date.today().year


def _parse_michess_date_range(line: str, title: str) -> tuple[str, str] | None:
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


def _extract_meta_content(markup: str, prop: str) -> str:
    # <meta property="og:title" content="...">
    m = re.search(
        rf'<meta\s+[^>]*(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']+)["\']',
        markup,
        flags=re.I,
    )
    return html.unescape(m.group(1)).strip() if m else ""


def parse_michess_event_detail(detail_html: str, source: dict[str, Any], url: str) -> dict[str, Any] | None:
    # Title: prefer og:title, then <h1>
    title = _extract_meta_content(detail_html, "og:title")
    if title:
        title = re.sub(r"\s*\|\s*Michigan Chess.*$", "", title, flags=re.I).strip()

    if not title:
        m_h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", detail_html, flags=re.I | re.S)
        if m_h1:
            title = html.unescape(re.sub(r"<[^>]+>", " ", m_h1.group(1)))
            title = re.sub(r"\s+", " ", title).strip()

    if not title:
        return None

    lines = _strip_html_to_lines(_extract_best_main_block(detail_html))

    # Date range near the top
    startDate = endDate = None
    for ln in lines[:120]:
        dr = _parse_michess_date_range(ln.strip(), title)
        if dr:
            startDate, endDate = dr
            break

    if not startDate:
        return None

    # Location: look for "City, ST" near "United States"
    city = "Unknown"
    state = "MI"
    for ln in lines[:240]:
        if "United States" in ln and "," in ln:
            mloc = re.search(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
            if mloc:
                city = mloc.group(1).strip()
                state = mloc.group(2).strip()
            break

    # Source text: keep main block only
    source_text = html_to_text_preserve_newlines(_extract_best_main_block(detail_html))

    return {
        "id": f"{source['id']}-{sanitize_slug(title)}-{startDate}",
        "name": title,
        "startDate": startDate,
        "endDate": endDate or startDate,
        "city": city,
        "state": state,
        "sourceId": source["id"],
        "sourceUrl": url,
        "sourceText": source_text[:20000],
    }


def parse_michess_events(listing_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    urls = _michess_extract_detail_urls(listing_html, source["homepage"])
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
# Orchestrator
# ----------------------------


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    parser = source["parser"]

    if parser == "uschess_upcoming":
        events: list[dict[str, Any]] = []

        # Try a reasonable number of pages; stop once parsing yields none.
        for page in range(0, 80):
            url = source["endpoint"] if page == 0 else f"{source['endpoint']}?page={page}"
            html_text = fetch_text(url)
            page_events = parse_uschess_upcoming(html_text, source)

            print(f"[uschess-upcoming] page={page} parsed={len(page_events)}")

            if not page_events and page > 0:
                break

            events.extend(page_events)

        # Enrich sourceText in parallel (this is the expensive part)
        enriched: list[dict[str, Any]] = []
        max_workers = 10
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(enrich_event_source_text, ev) for ev in events]
            total = len(futures)
            for idx, fut in enumerate(as_completed(futures), start=1):
                enriched.append(fut.result())
                if idx % 100 == 0 or idx == total:
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
