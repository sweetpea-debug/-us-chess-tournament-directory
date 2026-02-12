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

const state = {
  events: [],
  selectedState: "all",
  city: readStorage(CITY_KEY),
};

function sourceName(sourceId) {
  return SOURCE_CATALOG.find((source) => source.id === sourceId)?.name ?? "Unknown source";
}

function renderSources() {
  ui.sourceList.innerHTML = "";
  SOURCE_CATALOG.forEach((source) => {
    const li = document.createElement("li");
    li.innerHTML = `<a href="${source.homepage}" target="_blank" rel="noopener noreferrer">${source.name}</a> <span class="chip">${source.category}</span>`;
    ui.sourceList.appendChild(li);
  });
}

function renderStateFilter() {
  ui.stateFilter.innerHTML = '<option value="all">All states</option>';
  stateList(state.events).forEach((st) => {
    const option = document.createElement("option");
    option.value = st;
    option.textContent = st;
    ui.stateFilter.appendChild(option);
  });
  ui.stateFilter.value = state.selectedState;
}

function visibleEvents() {
  return state.events
    .filter((event) => (state.selectedState === "all" ? true : event.state === state.selectedState))
    .map((event) => {
      if (!state.city) return { ...event, distance: null };
      return {
        ...event,
        distance: haversineMiles(state.city.lat, state.city.lon, event.lat, event.lon),
      };
    })
    .filter((event) => (state.city ? event.distance <= SEARCH_RADIUS_MILES : true))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  const events = visibleEvents();
  ui.cards.innerHTML = "";

  if (!events.length) {
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
      sessionStorage.setItem("selectedTournament", JSON.stringify(event));
      window.location.href = `details.html?id=${encodeURIComponent(event.id)}`;
    });

    ui.cards.appendChild(node);
  });

  ui.resultCount.textContent = `${events.length} tournament${events.length === 1 ? "" : "s"}`;
}

function updateSyncLabel(isoDate) {
  ui.syncLabel.textContent = `Last sync: ${new Date(isoDate).toLocaleString()}`;
}

function cacheFresh(cache) {
  if (!cache?.syncedAt || !Array.isArray(cache.events)) return false;
  return Date.now() - new Date(cache.syncedAt).getTime() < CACHE_TTL_MS;
}

async function loadEvents({ force = false } = {}) {
  const cache = readStorage(CACHE_KEY);
  if (!force && cacheFresh(cache)) {
    state.events = cache.events;
    updateSyncLabel(cache.syncedAt);
    return;
  }

  // Baseline: seeded events. (Live source ingestion can be added next.)
  const events = [...FALLBACK_EVENTS];
  const syncedAt = new Date().toISOString();
  state.events = events;
  writeStorage(CACHE_KEY, { events, syncedAt });
  updateSyncLabel(syncedAt);
}

async function geocodeCity(queryText) {
  const query = new URLSearchParams({
    q: queryText,
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

function bindEvents() {
  ui.stateFilter.addEventListener("change", () => {
    state.selectedState = ui.stateFilter.value;
    renderCards();
  });

  ui.applyCity.addEventListener("click", async () => {
    const cityText = ui.cityInput.value.trim();
    if (!cityText) {
      ui.statusMessage.textContent = "Enter a city, e.g. Denver, CO.";
      return;
    }

    ui.statusMessage.textContent = "Finding city…";

    try {
      const city = await geocodeCity(cityText);
      if (!city) {
        ui.statusMessage.textContent = "City not found.";
        return;
      }

      state.city = city;
      writeStorage(CITY_KEY, city);
      ui.statusMessage.textContent = `Showing tournaments within ${SEARCH_RADIUS_MILES} miles of ${city.label}.`;
      renderCards();
    } catch {
      ui.statusMessage.textContent = "Geocoding failed. Try again.";
    }
  });

  ui.clearCity.addEventListener("click", () => {
    state.city = null;
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
  renderSources();
  bindEvents();

  if (state.city?.label) {
    ui.statusMessage.textContent = `Loaded saved city filter: ${state.city.label}`;
  }

  await loadEvents();
  renderStateFilter();
  renderCards();
}

init();

