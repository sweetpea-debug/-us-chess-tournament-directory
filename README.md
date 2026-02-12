# US Chess Tournament Radar

A standalone project for discovering U.S. chess tournaments with daily-published event data.

## What this website does

- Displays tournaments as clickable cards.
- Opens a dedicated details page with source links.
- Filters by U.S. state.
- Filters by events within 100 miles of a city.
- Shows a tracked source catalog.

## Daily server-side ingest

This repo now uses a **server-side daily ingest job** (GitHub Actions) that builds `events.json`.

- Workflow file: `.github/workflows/daily-events-ingest.yml`
- Ingest script: `scripts/build_events.py`
- Published feed consumed by the front-end: `events.json`

The front-end reads `events.json` first and falls back to seeded data if unavailable.

## Sources tracked

- US Chess Tournament Life Announcements
- US Chess Events
- FIDE Tournament Calendar
- ChessEvents
- Chess-Results
- Continental Chess Association
- Charlotte Chess Center
- Saint Louis Chess Club
- Vegas Chess Festival
- Chess Control

## Run locally

```bash
python3 -m http.server 8000
