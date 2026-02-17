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

const appState = {
  allEvents: [],
  selectedState: "all",
};

function updateSyncLabel(iso) {
  ui.syncLabel.textContent = `Last sync: ${new Date(iso).toLocaleString()}`;
}

async function fetchPublishedEvents() {
  const cacheBuster = Date.now();
  const response = await fetch(`events.json?v=${cacheBuster}`);
  if (!response.ok) throw new Error(`events.json not found (${response.status})`);

  const payload = await response.json();
  if (!Array.isArray(payload.events)) throw new Error("Invalid events.json payload");

  return {
    events: payload.events,
    syncedAt: payload.syncedAt || new Date().toISOString(),
  };
}

function renderStateFilter() {
  ui.stateFilter.innerHTML = '<option value="all">All states</option>';

  stateList(appState.allEvents)
    .filter((s) => s && s !== "US")
    .forEach((stateCode) => {
      const option = document.createElement("option");
      option.value = stateCode;
      option.textContent = stateCode;
      ui.stateFilter.appendChild(option);
    });

  ui.stateFilter.value = appState.selectedState;
}

function visibleEvents() {
  return appState.allEvents
    .filter((ev) => (appState.selectedState === "all" ? true : ev.state === appState.selectedState))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  const events = visibleEvents();
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

async function init() {
  ui.statusMessage.textContent = "Loading…";

  try {
    const published = await fetchPublishedEvents();
    appState.allEvents = published.events;
    updateSyncLabel(published.syncedAt);
    ui.statusMessage.textContent = "";
  } catch (err) {
    // fallback if events.json truly unavailable
    const syncedAt = new Date().toISOString();
    appState.allEvents = FALLBACK_EVENTS;
    updateSyncLabel(syncedAt);
    ui.statusMessage.textContent = "Could not load events.json. Using fallback dataset.";
  }

  renderStateFilter();
  renderCards();

  ui.stateFilter.addEventListener("change", () => {
    appState.selectedState = ui.stateFilter.value;
    renderCards();
  });
}

init();
