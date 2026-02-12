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
  window.__radarBooted = true;
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
