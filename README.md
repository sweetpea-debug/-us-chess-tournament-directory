## `README.md`
```md
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
```

Open:

- `http://127.0.0.1:8000/index.html`

## If you are updating files from GitHub web UI

Do **not** copy/paste raw red/green diff output line-by-line.

Use one of these safer options:

1. **Open each changed file directly** and replace the full file content with the latest version.
2. If you have a Pull Request, use **Files changed → ... → View file** to copy the final file contents (not the diff markers).
3. Commit each file update in GitHub's editor, then verify the deployed GitHub Pages site.

Tip: a diff is a *comparison format*, not a runnable file format.

Need click-by-click help? See [`HOW_TO_UPDATE_IN_GITHUB_UI.md`](./HOW_TO_UPDATE_IN_GITHUB_UI.md).
If you only see **"Copy patch"** / **"Copy git apply"** in chat, follow the fallback steps in [`HOW_TO_UPDATE_IN_GITHUB_UI.md`](./HOW_TO_UPDATE_IN_GITHUB_UI.md).
```

