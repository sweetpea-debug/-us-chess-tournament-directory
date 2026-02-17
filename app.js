// app.js
import { FALLBACK_EVENTS } from "./data.js";
import { formatDateRange, stateList } from "./utils.js";

const ui = {
  stateFilter: document.getElementById("state-filter"),
  resultCount: document.getElementById("result-count"),
  syncLabel: document.getElementById("sync-label"),
  statusMessage: document.getElementById("status-message"),
  cards: document.getElementById("cards"),
  template: document.getElementById("card-template"),
};

// NOTE: We keep a “last-known-good” cache, but we DO NOT cache fallback/empty results.
const CACHE_KEY = "tournamentRadarCacheV2";

const appState = {
  allEvents: [],
  selectedState: "all",
};

function updateSyncLabel(iso) {
  ui.syncLabel.textContent = `Last sync: ${new Date(iso).toLocaleString()}`;
}

function readCache() {
  try {
    return JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
  } catch {
    return null;
  }
}

function writeCache(payload) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch {
    // ignore storage failures
  }
}

async function fetchPublishedEvents() {
  // Cache-bust so GitHub Pages/browser doesn’t hand us a stale events.json
  const cacheBuster = Date.now();
  const response = await fetch(`events.json?v=${cacheBuster}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`events.json fetch failed (${response.status})`);

  const payload = await response.json();
  if (!Array.isArray(payload.events)) throw new Error("Invalid events.json payload");
  return payload;
}

async function loadEvents() {
  ui.statusMessage.textContent = "";

  try {
    const published = await fetchPublishedEvents();
    appState.allEvents = published.events;
    updateSyncLabel(published.syncedAt || new Date().toISOString());

    // Only cache if it’s real data
    if (published.events && published.events.length > 0) {
      writeCache({ syncedAt: published.syncedAt, events: published.events });
    }

    ui.statusMessage.textContent = "Loaded events from published feed.";
    return;
  } catch (err) {
    const cached = readCache();
    if (cached?.events?.length) {
      appState.allEvents = cached.events;
      updateSyncLabel(cached.syncedAt || new Date().toISOString());
      ui.statusMessage.textContent =
        "Could not load events.json right now. Showing last saved data.";
      return;
    }

    // No good cache available -> show fallback, but DO NOT cache it
    const syncedAt = new Date().toISOString();
    appState.allEvents = FALLBACK_EVENTS;
    updateSyncLabel(syncedAt);
    ui.statusMessage.textContent = "Could not load events.json. No saved data available.";
  }
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
  return (appState.allEvents || [])
    .filter((event) => (appState.selectedState === "all" ? true : event.state === appState.selectedState))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  const events = computeVisibleEvents();
  ui.cards.innerHTML = "";

  if (events.length === 0) {
    ui.cards.innerHTML = '<p class="muted">No tournaments match your current filter.</p>';
  }

  events.forEach((event) => {
    const node = ui.template.content.cloneNode(true);
    node.querySelector(".card__title").textContent = event.name || "Untitled event";
    node.querySelector(".card__dates").textContent = formatDateRange(event.startDate, event.endDate);
    node.querySelector(".card__location").textContent = `${event.city || "Unknown"}, ${event.state || "US"}`;

    node.querySelector(".card__open").addEventListener("click", () => {
      sessionStorage.setItem("usChessSelectedTournament", JSON.stringify(event));
      window.location.href = `details.html?id=${encodeURIComponent(event.id)}`;
    });

    ui.cards.appendChild(node);
  });

  ui.resultCount.textContent = `${events.length} tournament${events.length === 1 ? "" : "s"}`;
}

function bind() {
  ui.stateFilter.addEventListener("change", () => {
    appState.selectedState = ui.stateFilter.value;
    renderCards();
  });
}

async function init() {
  bind();
  await loadEvents();
  renderStateFilter();
  renderCards();
}

init();
