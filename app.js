# Full final file contents (copy/paste bundle)

## `.github/workflows/daily-events-ingest.yml`
```yaml
name: Daily events ingest

on:
  schedule:
    - cron: "15 9 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build-events:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Build events.json
        run: python scripts/build_events.py

      - name: Commit updates
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add events.json
          if git diff --staged --quiet; then
            echo "No changes to commit"
            exit 0
          fi
          git commit -m "chore: update daily events feed"
          git push
```

## `HOW_TO_UPDATE_IN_GITHUB_UI.md`
```md
# How to update this project using GitHub in your browser (no terminal)

This guide is for beginners who edit files directly on GitHub.com.

## Important rule first

- **Do not paste red/green diff text into files.**
- Diffs are just change previews.
- Always copy the **final file content**.

## Step-by-step workflow

1. Open your repository on GitHub.
2. Open the Pull Request that contains the changes you want.
3. Click **Files changed**.
4. For each changed file:
   - Click the `...` menu on that file panel.
   - Click **View file** (this shows clean file content, not diff markup).
   - Copy the file content.
5. Go back to your repo and open the matching file on your branch.
6. Click the pencil icon (**Edit this file**).
7. Select all existing file text and paste the copied content.
8. Scroll down to **Commit changes...**.
9. Use a message like `Update <filename> from PR`.
10. Click **Commit changes**.
11. Repeat for all changed files.

## After updating files

1. Go to **Settings → Pages**.
2. Confirm your Pages site is deployed from `main` and `/(root)`.
3. Wait 1–3 minutes after commits.
4. Open the Pages URL and verify:
   - cards render,
   - filters work,
   - details page opens.

## Troubleshooting

- If the site looks old, do a hard refresh (`Ctrl+Shift+R` or `Cmd+Shift+R`).
- If Pages URL is missing, check **Settings → Pages** and **Actions** for deployment status.
- If a file looks broken, compare it against **View file** in the PR again (not the diff view).

## If your Codex/Chat view only shows "Copy patch" and "Copy git apply"

That is normal in some chat UIs. In that case, use this beginner-safe approach:

1. **Do not** paste the raw patch into your GitHub files.
2. Ask for the **full final content of each file** (for example: `app.js`, `index.html`, `styles.css`).
3. In GitHub, open that file → click the pencil icon (**Edit this file**) → replace all text.
4. Click **Commit changes**.
5. Repeat for each file.

Why: patch format includes metadata (`diff --git`, `@@`, `+`, `-`) and is not the same as final file content.
```

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

## `index.html`
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>US Chess Tournament Radar</title>
    <meta
      name="description"
      content="Daily-updating United States chess tournament directory with source aggregation, state filters, and 100-mile city search."
    />
    <link rel="stylesheet" href="styles.css" />
  </head>
  <body>
    <header class="hero">
      <div class="container">
        <p class="hero__eyebrow">UNITED STATES CHESS TOURNAMENT RADAR</p>
        <h1>Find tournaments across the United States</h1>
        <p class="hero__subtitle">
          Aggregated from federation calendars, organizer pages, and event platforms. Data refreshes
          every 24 hours.
        </p>
        <div class="hero__meta">
          <span id="sync-label">Last sync: never</span>
          <button id="refresh-button" type="button" class="btn btn--secondary">Refresh now</button>
        </div>
      </div>
    </header>

    <main class="container layout">
      <aside class="panel" aria-label="Filters">
        <h2>Filters</h2>

        <label class="field" for="state-filter">
          <span>US state</span>
          <select id="state-filter">
            <option value="all">All states</option>
          </select>
        </label>

        <label class="field" for="city-input">
          <span>Within 100 miles of city</span>
          <input id="city-input" type="text" placeholder="Dallas, TX" autocomplete="off" />
        </label>

        <div class="row">
          <button id="apply-city" type="button" class="btn">Apply</button>
          <button id="clear-city" type="button" class="btn btn--secondary">Clear</button>
        </div>

        <p id="status-message" class="muted" aria-live="polite"></p>

        <section class="sources" aria-label="Tracked online sources">
          <h3>Tracked online sources</h3>
          <ul id="source-list"></ul>
        </section>
      </aside>

      <section class="panel" aria-label="Tournament results">
        <div class="results-head">
          <h2>Tournaments</h2>
          <p id="result-count" class="muted">Loading…</p>
        </div>

        <div id="cards" class="cards"></div>
      </section>
    </main>

    <template id="card-template">
      <article class="card">
        <h3 class="card__title"></h3>
        <p class="card__dates"></p>
        <p class="card__location"></p>
        <p class="card__format"></p>
        <div class="chips"></div>
        <button type="button" class="btn card__open">Open details</button>
      </article>
    </template>

    <script type="module" src="app.js"></script>
  </body>
</html>
```

## `styles.css`
```css
:root {
  --bg: #09101d;
  --panel: #111a2f;
  --panel-soft: #172543;
  --line: #2a406e;
  --text: #ecf2ff;
  --muted: #9ab0db;
  --primary: #4a8eff;
  --secondary: #2d3f64;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: radial-gradient(circle at top, #121e36, var(--bg));
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
}

.container {
  width: min(1120px, 92%);
  margin: 0 auto;
}

.hero {
  border-bottom: 1px solid var(--line);
  background: linear-gradient(140deg, #091428 0%, #153262 100%);
}

.hero .container {
  padding: 2rem 0;
}

.hero__eyebrow {
  margin: 0;
  font-size: 0.78rem;
  letter-spacing: 0.12em;
  color: #c6dbff;
}

.hero h1 {
  margin: 0.35rem 0 0;
}

.hero__subtitle {
  max-width: 74ch;
  color: var(--muted);
}

.hero__meta {
  display: flex;
  gap: 0.8rem;
  flex-wrap: wrap;
  align-items: center;
}

.layout {
  margin-top: 1.2rem;
  margin-bottom: 2rem;
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 1rem;
}

.panel {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: color-mix(in oklab, var(--panel) 86%, black);
  padding: 1rem;
}

h2,
h3 {
  margin-top: 0;
}

.field {
  display: grid;
  gap: 0.4rem;
  margin-bottom: 0.9rem;
}

.field span {
  color: var(--muted);
  font-weight: 600;
}

input,
select,
button {
  font: inherit;
}

input,
select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel-soft);
  color: var(--text);
  padding: 0.63rem 0.74rem;
}

.row {
  display: flex;
  gap: 0.6rem;
}

.btn {
  border: 0;
  border-radius: 10px;
  background: var(--primary);
  color: white;
  padding: 0.62rem 0.9rem;
  cursor: pointer;
}

.btn--secondary {
  background: var(--secondary);
}

.btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.muted {
  color: var(--muted);
}

.sources ul {
  margin: 0;
  padding-left: 1.1rem;
  display: grid;
  gap: 0.45rem;
}

.sources a,
.back-link,
a {
  color: #bdd8ff;
}

.results-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0.85rem;
}

.card {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel-soft);
  padding: 0.85rem;
  display: grid;
  gap: 0.45rem;
}

.card p,
.card h3 {
  margin: 0;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.chip {
  font-size: 0.75rem;
  border: 1px solid #4265a4;
  border-radius: 999px;
  padding: 0.2rem 0.55rem;
  background: #21365f;
  color: #d7e8ff;
}

.details-layout {
  margin-top: 1.2rem;
  margin-bottom: 2rem;
  display: grid;
  gap: 0.8rem;
}

.back-link {
  text-decoration: none;
  font-weight: 600;
}

@media (max-width: 940px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
```

## `app.js`
```js
import { FALLBACK_EVENTS, SOURCE_CATALOG } from "./data.js";
import {
  CACHE_KEY,
  CACHE_TTL_MS,
  CITY_KEY,
  SEARCH_RADIUS_MILES,
  formatDateRange,
  haversineMiles,
  readStorage,
  stateList,
  writeStorage,
} from "./utils.js";

const ui = {
  stateFilter: document.getElementById("state-filter"),
  cityInput: document.getElementById("city-input"),
  applyCity: document.getElementById("apply-city"),
  clearCity: document.getElementById("clear-city"),
  refreshButton: document.getElementById("refresh-button"),
  sourceList: document.getElementById("source-list"),
  resultCount: document.getElementById("result-count"),
  syncLabel: document.getElementById("sync-label"),
  statusMessage: document.getElementById("status-message"),
  cards: document.getElementById("cards"),
  template: document.getElementById("card-template"),
};

const appState = {
  allEvents: [],
  selectedState: "all",
  city: readStorage(CITY_KEY),
};

function sourceName(sourceId) {
  return SOURCE_CATALOG.find((source) => source.id === sourceId)?.name ?? "Unknown source";
}

function renderSourceCatalog() {
  ui.sourceList.innerHTML = "";

  SOURCE_CATALOG.forEach((source) => {
    const li = document.createElement("li");
    li.innerHTML = `<a href="${source.homepage}" target="_blank" rel="noopener noreferrer">${source.name}</a> <span class="chip">${source.category}</span>`;
    ui.sourceList.appendChild(li);
  });
}

function renderStateFilter() {
  ui.stateFilter.innerHTML = '<option value="all">All states</option>';

  stateList(appState.allEvents)
    .filter((stateCode) => stateCode !== "US")
    .forEach((stateCode) => {
      const option = document.createElement("option");
      option.value = stateCode;
      option.textContent = stateCode;
      ui.stateFilter.appendChild(option);
    });

  ui.stateFilter.value = appState.selectedState;
}

function computeVisibleEvents() {
  return appState.allEvents
    .filter((event) => (appState.selectedState === "all" ? true : event.state === appState.selectedState))
    .map((event) => {
      if (!appState.city) {
        return { ...event, distance: null };
      }

      return {
        ...event,
        distance: haversineMiles(appState.city.lat, appState.city.lon, event.lat, event.lon),
      };
    })
    .filter((event) => (appState.city ? event.distance <= SEARCH_RADIUS_MILES : true))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  const events = computeVisibleEvents();
  ui.cards.innerHTML = "";

  if (events.length === 0) {
    ui.cards.innerHTML = '<p class="muted">No tournaments match your current filters.</p>';
  }

  events.forEach((event) => {
    const node = ui.template.content.cloneNode(true);
    node.querySelector(".card__title").textContent = event.name;
    node.querySelector(".card__dates").textContent = formatDateRange(event.startDate, event.endDate);
    node.querySelector(".card__location").textContent = `${event.venue} - ${event.city}, ${event.state}`;
    node.querySelector(".card__format").textContent = event.format;

    const chips = node.querySelector(".chips");

    const sourceChip = document.createElement("span");
    sourceChip.className = "chip";
    sourceChip.textContent = sourceName(event.sourceId);
    chips.appendChild(sourceChip);

    if (event.distance !== null) {
      const distanceChip = document.createElement("span");
      distanceChip.className = "chip";
      distanceChip.textContent = `${event.distance.toFixed(1)} miles`;
      chips.appendChild(distanceChip);
    }

    node.querySelector(".card__open").addEventListener("click", () => {
      sessionStorage.setItem("usChessSelectedTournament", JSON.stringify(event));
      window.location.href = `details.html?id=${encodeURIComponent(event.id)}`;
    });

    ui.cards.appendChild(node);
  });

  ui.resultCount.textContent = `${events.length} tournament${events.length === 1 ? "" : "s"}`;
}

function updateSyncLabel(iso) {
  ui.syncLabel.textContent = `Last sync: ${new Date(iso).toLocaleString()}`;
}

function cacheIsFresh(cache) {
  if (!cache?.syncedAt || !Array.isArray(cache.events)) {
    return false;
  }

  return Date.now() - new Date(cache.syncedAt).getTime() < CACHE_TTL_MS;
}

async function fetchPublishedEvents() {
  const cacheBuster = Date.now();
  const response = await fetch(`events.json?v=${cacheBuster}`);

  if (!response.ok) {
    throw new Error("No published events.json yet");
  }

  const payload = await response.json();
  if (!Array.isArray(payload.events)) {
    throw new Error("Invalid events.json payload");
  }

  return {
    events: payload.events,
    syncedAt: payload.syncedAt || new Date().toISOString(),
  };
}

async function loadEvents({ force = false } = {}) {
  const cached = readStorage(CACHE_KEY);

  if (!force && cacheIsFresh(cached)) {
    appState.allEvents = cached.events;
    updateSyncLabel(cached.syncedAt);
    return;
  }

  try {
    const published = await fetchPublishedEvents();
    appState.allEvents = published.events;
    writeStorage(CACHE_KEY, published);
    updateSyncLabel(published.syncedAt);
    ui.statusMessage.textContent = "Loaded events from daily published feed.";
  } catch {
    const syncedAt = new Date().toISOString();
    const fallbackPayload = { events: FALLBACK_EVENTS, syncedAt };
    appState.allEvents = FALLBACK_EVENTS;
    writeStorage(CACHE_KEY, fallbackPayload);
    updateSyncLabel(syncedAt);
    ui.statusMessage.textContent = "Using fallback dataset. Daily feed unavailable right now.";
  }
}

async function geocodeCity(value) {
  const query = new URLSearchParams({
    q: value,
    format: "jsonv2",
    countrycodes: "us",
    limit: "1",
  });

  const response = await fetch(`https://nominatim.openstreetmap.org/search?${query.toString()}`);
  if (!response.ok) {
    throw new Error("Geocoder failed");
  }

  const rows = await response.json();
  if (!rows.length) {
    return null;
  }

  return {
    label: rows[0].display_name,
    lat: Number(rows[0].lat),
    lon: Number(rows[0].lon),
  };
}

function bind() {
  ui.stateFilter.addEventListener("change", () => {
    appState.selectedState = ui.stateFilter.value;
    renderCards();
  });

  ui.applyCity.addEventListener("click", async () => {
    const city = ui.cityInput.value.trim();
    if (!city) {
      ui.statusMessage.textContent = "Enter a city, for example: Denver, CO";
      return;
    }

    ui.statusMessage.textContent = "Resolving city…";

    try {
      const location = await geocodeCity(city);
      if (!location) {
        ui.statusMessage.textContent = "City not found.";
        return;
      }

      appState.city = location;
      writeStorage(CITY_KEY, location);
      ui.statusMessage.textContent = `Showing tournaments within ${SEARCH_RADIUS_MILES} miles of ${location.label}.`;
      renderCards();
    } catch {
      ui.statusMessage.textContent = "Could not geocode city right now. Try again shortly.";
    }
  });

  ui.clearCity.addEventListener("click", () => {
    appState.city = null;
    localStorage.removeItem(CITY_KEY);
    ui.cityInput.value = "";
    ui.statusMessage.textContent = "City filter cleared.";
    renderCards();
  });

  ui.refreshButton.addEventListener("click", async () => {
    ui.refreshButton.disabled = true;
    ui.refreshButton.textContent = "Refreshing…";

    await loadEvents({ force: true });
    renderStateFilter();
    renderCards();

    ui.refreshButton.textContent = "Refresh now";
    ui.refreshButton.disabled = false;
  });
}

async function init() {
  renderSourceCatalog();
  bind();

  if (appState.city?.label) {
    ui.statusMessage.textContent = `Loaded saved city filter: ${appState.city.label}`;
  }

  await loadEvents();
  renderStateFilter();
  renderCards();
}

init();
```
