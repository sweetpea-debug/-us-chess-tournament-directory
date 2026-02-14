import { FALLBACK_EVENTS } from "./data.js";
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

function safeSetText(node, text) {
  if (node) node.textContent = text;
}

function renderStateFilter() {
  if (!ui.stateFilter) return;

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
      if (!appState.city) return { ...event, distance: null };
      return {
        ...event,
        distance: haversineMiles(appState.city.lat, appState.city.lon, event.lat, event.lon),
      };
    })
    .filter((event) => (appState.city ? event.distance <= SEARCH_RADIUS_MILES : true))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  if (!ui.cards || !ui.template) return;

  const events = computeVisibleEvents();
  ui.cards.innerHTML = "";

  if (events.length === 0) {
    ui.cards.innerHTML = '<p class="muted">No tournaments match your current filters.</p>';
    safeSetText(ui.resultCount, "0 tournaments");
    return;
  }

  events.forEach((event) => {
    const node = ui.template.content.cloneNode(true);
    node.querySelector(".card__title").textContent = event.name || "Untitled tournament";
    node.querySelector(".card__dates").textContent = formatDateRange(event.startDate, event.endDate);
    const venue = (event.venue && event.venue !== "See source listing") ? event.venue : "";
const format = (event.format && event.format !== "See source listing") ? event.format : "";
const locLine = venue
  ? `${venue} — ${event.city}, ${event.state}`
  : `${event.city}, ${event.state}`;

node.querySelector(".card__location").textContent = locLine;
node.querySelector(".card__format").textContent = format || "";

    const chips = node.querySelector(".chips");
    // Only show distance chip (no source chip)
    if (event.distance !== null && Number.isFinite(event.distance)) {
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

  safeSetText(ui.resultCount, `${events.length} tournament${events.length === 1 ? "" : "s"}`);
}

function updateSyncLabel(iso) {
  if (!ui.syncLabel) return;
  const dt = new Date(iso);
  ui.syncLabel.textContent = `Last sync: ${Number.isNaN(dt.getTime()) ? "unknown" : dt.toLocaleString()}`;
}

function cacheIsFresh(cache) {
  if (!cache?.syncedAt || !Array.isArray(cache.events)) return false;
  return Date.now() - new Date(cache.syncedAt).getTime() < CACHE_TTL_MS;
}

async function fetchPublishedEvents() {
  const cacheBuster = Date.now();
  const response = await fetch(`events.json?v=${cacheBuster}`, { cache: "no-store" });

  if (!response.ok) {
    throw new Error(`No published events.json yet (HTTP ${response.status})`);
  }

  const payload = await response.json();
  if (!Array.isArray(payload.events)) {
    throw new Error("Invalid events.json payload (missing events array)");
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
    safeSetText(ui.statusMessage, "Loaded events from cached data.");
    return;
  }

  try {
    const published = await fetchPublishedEvents();
    appState.allEvents = published.events;
    writeStorage(CACHE_KEY, published);
    updateSyncLabel(published.syncedAt);
    safeSetText(ui.statusMessage, "Loaded events from daily published feed.");
  } catch (err) {
    const syncedAt = new Date().toISOString();
    const fallbackPayload = { events: FALLBACK_EVENTS, syncedAt };
    appState.allEvents = FALLBACK_EVENTS;
    writeStorage(CACHE_KEY, fallbackPayload);
    updateSyncLabel(syncedAt);
    safeSetText(ui.statusMessage, "Using fallback dataset. Daily feed unavailable right now.");
    // Helpful for debugging if you open DevTools
    console.warn("Falling back to local dataset:", err);
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
  if (!response.ok) throw new Error("Geocoder failed");

  const rows = await response.json();
  if (!rows.length) return null;

  return {
    label: rows[0].display_name,
    lat: Number(rows[0].lat),
    lon: Number(rows[0].lon),
  };
}

function bind() {
  ui.stateFilter?.addEventListener("change", () => {
    appState.selectedState = ui.stateFilter.value;
    renderCards();
  });

  ui.applyCity?.addEventListener("click", async () => {
    const city = ui.cityInput?.value?.trim() || "";
    if (!city) {
      safeSetText(ui.statusMessage, "Enter a city, for example: Denver, CO");
      return;
    }

    safeSetText(ui.statusMessage, "Resolving city…");

    try {
      const location = await geocodeCity(city);
      if (!location) {
        safeSetText(ui.statusMessage, "City not found.");
        return;
      }

      appState.city = location;
      writeStorage(CITY_KEY, location);
      safeSetText(ui.statusMessage, `Showing tournaments within ${SEARCH_RADIUS_MILES} miles of ${location.label}.`);
      renderCards();
    } catch {
      safeSetText(ui.statusMessage, "Could not geocode city right now. Try again shortly.");
    }
  });

  ui.clearCity?.addEventListener("click", () => {
    appState.city = null;
    localStorage.removeItem(CITY_KEY);
    if (ui.cityInput) ui.cityInput.value = "";
    safeSetText(ui.statusMessage, "City filter cleared.");
    renderCards();
  });

  ui.refreshButton?.addEventListener("click", async () => {
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
  window.__radarBooted = true;
  bind();

  if (appState.city?.label) {
    safeSetText(ui.statusMessage, `Loaded saved city filter: ${appState.city.label}`);
  }

  await loadEvents();
  renderStateFilter();
  renderCards();
}

init();
