import { CACHE_KEY, CACHE_TTL_MS, formatDateRange, readStorage, stateList, writeStorage } from "./utils.js";

const ui = {
  stateFilter: document.getElementById("state-filter"),
  resultCount: document.getElementById("result-count"),
  syncLabel: document.getElementById("sync-label"),
  statusMessage: document.getElementById("status-message"),
  cards: document.getElementById("cards"),
  template: document.getElementById("card-template"),
};

const appState = {
  allEvents: [],
  selectedState: "all",
};

function updateSyncLabel(iso) {
  ui.syncLabel.textContent = `Last sync: ${new Date(iso).toLocaleString()}`;
}

function cacheIsFresh(cache) {
  if (!cache?.syncedAt || !Array.isArray(cache.events)) return false;
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

async function loadEvents() {
  const cached = readStorage(CACHE_KEY);

  if (cacheIsFresh(cached)) {
    appState.allEvents = cached.events;
    updateSyncLabel(cached.syncedAt);
    ui.statusMessage.textContent = "Loaded events from cached data.";
    return;
  }

  try {
    const published = await fetchPublishedEvents();
    appState.allEvents = published.events;
    writeStorage(CACHE_KEY, published);
    updateSyncLabel(published.syncedAt);
    ui.statusMessage.textContent = "Loaded events from daily published feed.";
  } catch {
    appState.allEvents = [];
    ui.statusMessage.textContent =
      "Could not load events.json. Run the GitHub Action and confirm events.json exists at repo root.";
  }
}

function renderStateFilter() {
  ui.stateFilter.innerHTML = '<option value="all">All states</option>';

  stateList(appState.allEvents)
    .filter((stateCode) => stateCode && stateCode !== "US")
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
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  const events = computeVisibleEvents();
  ui.cards.innerHTML = "";

  if (!events.length) {
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
  window.__radarBooted = true;
  bind();
  await loadEvents();
  renderStateFilter();
  renderCards();
}

init();
