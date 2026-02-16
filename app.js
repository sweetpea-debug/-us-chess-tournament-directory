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

function computeVisibleEvents() {
  return appState.allEvents
    .filter((event) => (appState.selectedState === "all" ? true : event.state === appState.selectedState))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
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

function renderCards() {
  const events = computeVisibleEvents();
  ui.cards.innerHTML = "";

  if (events.length === 0) {
    ui.cards.innerHTML = '<p class="muted">No tournaments match your current filter.</p>';
  }

  events.forEach((event) => {
    const node = ui.template.content.cloneNode(true);
    node.querySelector(".card__title").textContent = event.name;
    node.querySelector(".card__dates").textContent = formatDateRange(event.startDate, event.endDate);
    node.querySelector(".card__location").textContent = `${event.city}, ${event.state}`;

    node.querySelector(".card__open").addEventListener("click", () => {
      sessionStorage.setItem("selectedTournament", JSON.stringify(event));
      window.location.href = `details.html?id=${encodeURIComponent(event.id)}`;
    });

    ui.cards.appendChild(node);
  });

  ui.resultCount.textContent = `${events.length} tournament${events.length === 1 ? "" : "s"}`;
}

async function loadEvents() {
  try {
    const cacheBuster = Date.now();
    const response = await fetch(`events.json?v=${cacheBuster}`);
    if (!response.ok) throw new Error("events.json not found");

    const payload = await response.json();
    if (!Array.isArray(payload.events)) throw new Error("Invalid events.json payload");

    appState.allEvents = payload.events;
    updateSyncLabel(payload.syncedAt || new Date().toISOString());
    ui.statusMessage.textContent = "";
  } catch {
    appState.allEvents = [];
    updateSyncLabel(new Date().toISOString());
    ui.statusMessage.textContent =
      "Could not load events.json yet. Run the ingest workflow and confirm it committed events.json to the repo root.";
  }
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
