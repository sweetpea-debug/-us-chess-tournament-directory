import { SOURCE_CATALOG } from "./data.js";
import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function sourceById(sourceId) {
  return SOURCE_CATALOG.find((source) => source.id === sourceId);
}

function renderMissing() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Go back and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  const source = sourceById(event.sourceId);
  detailsRoot.innerHTML = `
    <h1>${event.name}</h1>
    <p><strong>Dates:</strong> ${formatDateRange(event.startDate, event.endDate)}</p>
    <p><strong>Location:</strong> ${event.venue}, ${event.city}, ${event.state}</p>
    <p><strong>Format:</strong> ${event.format}</p>
    <p><strong>Time control:</strong> ${event.timeControl || "See source"}</p>
    <p><strong>Sections:</strong> ${(event.sections || []).join(", ") || "See source"}</p>
    <p><strong>Entry fee:</strong> ${event.entryFee || "See source"}</p>
    <p><strong>Source:</strong> ${source?.name || "Unknown source"}</p>
    <p><a href="${event.sourceUrl}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>
  `;
}

function init() {
  const params = new URLSearchParams(window.location.search);
  const eventId = params.get("id");
  const raw = sessionStorage.getItem("selectedTournament");

  if (!raw) {
    renderMissing();
    return;
  }

  try {
    const event = JSON.parse(raw);
    if (!eventId || event.id !== eventId) {
      renderMissing();
      return;
    }
    renderTournament(event);
  } catch {
    renderMissing();
  }
}

init();

