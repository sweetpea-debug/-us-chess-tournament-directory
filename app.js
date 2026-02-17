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

async function fetchEventsJson() {
  const cacheBuster = Date.now();
  const resp = await fetch(`events.json?v=${cacheBuster}`);
  if (!resp.ok) throw new Error(`events.json HTTP ${resp.status}`);
  const payload = await resp.json();
  if (!payload || !Array.isArray(payload.events)) throw new Error("Invalid events.json payload");
  return payload;
}

function renderStateFilter() {
  ui.stateFilter.innerHTML = '<option value="all">All states</option>';

  stateList(appState.allEvents)
    .filter((st) => st && st !== "US")
    .forEach((st) => {
      const opt = document.createElement("option");
      opt.value = st;
      opt.textContent = st;
      ui.stateFilter.appendChild(opt);
    });

  ui.stateFilter.value = appState.selectedState;
}

function computeVisibleEvents() {
  return appState.allEvents
    .filter((e) => (appState.selectedState === "all" ? true : e.state === appState.selectedState))
    .sort((a, b) => new Date(a.startDate) - new Date(b.startDate));
}

function renderCards() {
  const events = computeVisibleEvents();
  ui.cards.innerHTML = "";

  if (!events.length) {
    ui.cards.innerHTML = '<p class="muted">No tournaments match your current filter.</p>';
  }

  for (const event of events) {
    const node = ui.template.content.cloneNode(true);
    node.querySelector(".card__title").textContent = event.name || "Untitled event";
    node.querySelector(".card__dates").textContent = formatDateRange(event.startDate, event.endDate);
    node.querySelector(".card__location").textContent = `${event.city || "Unknown"}, ${event.state || "US"}`;

    node.querySelector(".card__open").addEventListener("click", () => {
      sessionStorage.setItem("usChessSelectedTournament", JSON.stringify(event));
      window.location.href = `details.html?id=${encodeURIComponent(event.id)}`;
    });

    ui.cards.appendChild(node);
  }

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

  try {
    const payload = await fetchEventsJson();
    appState.allEvents = payload.events;
    updateSyncLabel(payload.syncedAt || new Date().toISOString());
    ui.statusMessage.textContent = "";
  } catch (err) {
    // fallback
    appState.allEvents = FALLBACK_EVENTS;
    updateSyncLabel(new Date().toISOString());
    ui.statusMessage.textContent = "Could not load events.json. Using fallback dataset (may be empty).";
    console.error(err);
  }

  renderStateFilter();
  renderCards();
}

init();
