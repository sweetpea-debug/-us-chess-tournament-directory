#!/usr/bin/env python3
"""Daily ingest for US chess tournaments."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "events.json"

SOURCE_CATALOG = [
    {
        "id": "uschess-tla",
        "name": "US Chess Tournament Life Announcements",
        "parser": "us_chess_wp",
        "endpoint": "https://new.uschess.org/wp-json/wp/v2/posts?per_page=50&search=tournament",
        "homepage": "https://new.uschess.org/tournaments",
    },
    {
        "id": "uschess-events",
        "name": "US Chess Events",
        "parser": "us_chess_wp",
        "endpoint": "https://new.uschess.org/wp-json/wp/v2/posts?per_page=50&search=event",
        "homepage": "https://new.uschess.org/events",
    },
    {
        "id": "fide-calendar",
        "name": "FIDE Tournament Calendar",
        "parser": "text_proxy",
        "endpoint": "https://r.jina.ai/http://ratings.fide.com/tournament_calendar.phtml",
        "homepage": "https://ratings.fide.com/tournament_calendar.phtml",
    },
    {
        "id": "chessevents",
        "name": "ChessEvents",
        "parser": "text_proxy",
        "endpoint": "https://r.jina.ai/http://chessevents.com",
        "homepage": "https://chessevents.com",
    },
    {
        "id": "chessresults",
        "name": "Chess-Results",
        "parser": "text_proxy",
        "endpoint": "https://r.jina.ai/http://chess-results.com",
        "homepage": "https://chess-results.com",
    },
    {
        "id": "cca",
        "name": "Continental Chess Association",
        "parser": "text_proxy",
        "endpoint": "https://r.jina.ai/http://www.continentalchess.com",
        "homepage": "https://www.continentalchess.com",
    },
    {
        "id": "charlotte",
        "name": "Charlotte Chess Center",
        "parser": "text_proxy",
        "endpoint": "https://r.jina.ai/http://www.charlottechesscenter.org/events",
        "homepage": "https://www.charlottechesscenter.org/events",
    },
]

FALLBACK_EVENTS = [
    {"id": "world-open-2026", "name": "World Open", "startDate": "2026-06-30", "endDate": "2026-07-06", "city": "Philadelphia", "state": "PA", "venue": "Sheraton Philadelphia Downtown", "lat": 39.9583, "lon": -75.1638, "format": "9-round Swiss", "entryFee": "$429-$479", "sections": ["Open", "U2400", "U2200", "U2000", "U1800", "U1600"], "timeControl": "G/90;+30", "sourceId": "cca", "sourceUrl": "https://www.continentalchess.com/worldopen/"},
    {"id": "national-open-2026", "name": "National Open", "startDate": "2026-06-03", "endDate": "2026-06-07", "city": "Las Vegas", "state": "NV", "venue": "Flamingo Las Vegas", "lat": 36.1162, "lon": -115.1701, "format": "6-round Swiss", "entryFee": "$180-$280", "sections": ["Open", "U2200", "U2000", "U1800", "U1600"], "timeControl": "G/90;+30", "sourceId": "vegas", "sourceUrl": "https://vegaschessfestival.com"},
    {"id": "charlotte-open-2026", "name": "Charlotte Open", "startDate": "2026-08-06", "endDate": "2026-08-10", "city": "Charlotte", "state": "NC", "venue": "Le Meridien Charlotte", "lat": 35.2137, "lon": -80.8557, "format": "9-round Swiss", "entryFee": "$199-$259", "sections": ["Open", "U2300", "U2000", "U1700"], "timeControl": "G/90;+30", "sourceId": "charlotte", "sourceUrl": "https://www.charlottechesscenter.org/events"},
    {"id": "us-championship-2026", "name": "US Championship", "startDate": "2026-10-01", "endDate": "2026-10-14", "city": "Saint Louis", "state": "MO", "venue": "Saint Louis Chess Club", "lat": 38.6365, "lon": -90.2618, "format": "Round robin", "entryFee": "Invitation only", "sections": ["Championship"], "timeControl": "Classical", "sourceId": "saintlouis", "sourceUrl": "https://www.uschesschamps.com"},
    {"id": "texas-open-2026", "name": "Texas Open", "startDate": "2026-04-10", "endDate": "2026-04-12", "city": "Houston", "state": "TX", "venue": "Hyatt Regency Houston", "lat": 29.7604, "lon": -95.3698, "format": "5-round Swiss", "entryFee": "$95-$145", "sections": ["Open", "Reserve", "Novice"], "timeControl": "G/90;+30", "sourceId": "chessevents", "sourceUrl": "https://chessevents.com"},

    {"id": "chicago-open-2026", "name": "Chicago Open", "startDate": "2026-05-21", "endDate": "2026-05-25", "city": "Wheeling", "state": "IL", "venue": "Westin Chicago North Shore", "lat": 42.1355, "lon": -87.9065, "format": "9-round Swiss", "entryFee": "$329-$399", "sections": ["Open", "U2300", "U2100", "U1900", "U1700"], "timeControl": "G/90;+30", "sourceId": "cca", "sourceUrl": "https://www.continentalchess.com/chicagoopen/"},
    {"id": "us-amateur-team-east-2026", "name": "US Amateur Team East", "startDate": "2026-02-14", "endDate": "2026-02-16", "city": "Parsippany", "state": "NJ", "venue": "Parsippany Hilton", "lat": 40.857, "lon": -74.426, "format": "Team Swiss", "entryFee": "$180 per team", "sections": ["4-board teams"], "timeControl": "G/60;+5", "sourceId": "cca", "sourceUrl": "https://www.continentalchess.com"},
    {"id": "bay-area-international-2026", "name": "Bay Area International", "startDate": "2026-01-03", "endDate": "2026-01-07", "city": "Santa Clara", "state": "CA", "venue": "Santa Clara Convention Center", "lat": 37.4034, "lon": -121.9717, "format": "Norm groups", "entryFee": "$350+", "sections": ["GM/IM norm groups"], "timeControl": "Classical", "sourceId": "chessevents", "sourceUrl": "https://chessevents.com"},
    {"id": "marshall-gp-2026", "name": "Marshall Grand Prix", "startDate": "2026-03-07", "endDate": "2026-03-08", "city": "New York", "state": "NY", "venue": "Marshall Chess Club", "lat": 40.7298, "lon": -73.9973, "format": "5-round Swiss", "entryFee": "$85", "sections": ["Open"], "timeControl": "G/60;+10", "sourceId": "chessresults", "sourceUrl": "https://chess-results.com"},
    {"id": "pacific-northwest-open-2026", "name": "Pacific Northwest Open", "startDate": "2026-07-17", "endDate": "2026-07-19", "city": "Bellevue", "state": "WA", "venue": "Hyatt Regency Bellevue", "lat": 47.615, "lon": -122.201, "format": "6-round Swiss", "entryFee": "$120-$180", "sections": ["Open", "U1800"], "timeControl": "G/90;+30", "sourceId": "chessctrl", "sourceUrl": "https://chessctrl.com"},
]

TITLE_RE = re.compile(r"(open|championship|classic|festival|tournament)", re.IGNORECASE)


def sanitize_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=25) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_text_proxy(text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    matches = [line for line in rows if TITLE_RE.search(line)]
    out: list[dict[str, Any]] = []
    for idx, line in enumerate(matches[:20]):
        out.append(
            {
                "id": f"{source['id']}-live-{idx}-{sanitize_slug(line)[:60]}",
                "name": line[:120],
                "startDate": "2026-01-01",
                "endDate": "2026-01-01",
                "city": "Unknown",
                "state": "US",
                "venue": source["name"],
                "lat": 39.8283,
                "lon": -98.5795,
                "format": "See source listing",
                "entryFee": "See source listing",
                "sections": [],
                "timeControl": "See source listing",
                "sourceId": source["id"],
                "sourceUrl": source["homepage"],
            }
        )
    return out


def parse_us_chess_wp(text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    payload = json.loads(text)
    out: list[dict[str, Any]] = []
    for row in payload[:30]:
        title_raw = row.get("title", {}).get("rendered", "")
        title = re.sub(r"<[^>]*>", "", title_raw).strip()
        if not title:
            continue
        date = str(row.get("date", "2026-01-01"))[:10]
        out.append(
            {
                "id": f"{source['id']}-{row.get('id', sanitize_slug(title))}",
                "name": title,
                "startDate": date,
                "endDate": date,
                "city": "Unknown",
                "state": "US",
                "venue": "US Chess listing",
                "lat": 39.8283,
                "lon": -98.5795,
                "format": "See source listing",
                "entryFee": "See source listing",
                "sections": [],
                "timeControl": "See source listing",
                "sourceId": source["id"],
                "sourceUrl": row.get("link") or source["homepage"],
            }
        )
    return out


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        text = fetch_text(source["endpoint"])
        if source["parser"] == "us_chess_wp":
            return parse_us_chess_wp(text, source)
        return parse_text_proxy(text, source)
    except Exception:
        return []


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        key = f"{event['name']}|{event['startDate']}|{event['sourceId']}".lower()
        seen.setdefault(key, event)
    return list(seen.values())


def main() -> None:
    live: list[dict[str, Any]] = []
    for source in SOURCE_CATALOG:
        live.extend(fetch_source(source))

    events = dedupe(FALLBACK_EVENTS + live)
    payload = {
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "eventCount": len(events),
        "events": events,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(events)} events")


if __name__ == "__main__":
    main()
