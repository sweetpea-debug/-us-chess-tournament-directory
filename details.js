import { SOURCE_CATALOG } from "./data.js";
import { escapeHtml, formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function resolveSource(sourceId) {
  return SOURCE_CATALOG.find((s) => s.id === sourceId);
}

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  const source = resolveSource(event.sourceId);

  const title = escapeHtml(event.name || "Untitled event");
  const dates = escapeHtml(formatDateRange(event.startDate, event.endDate));
  const location = escapeHtml(`${event.city || "Unknown"}, ${event.state || "US"}`);
  const sourceUrl = escapeHtml(event.sourceUrl || "#");

  const sourceText = (event.sourceText || "").trim();

  detailsRoot.innerHTML = `
    <h1>${title}</h1>

    <p><strong>Dates:</strong> ${dates}</p>
    <p><strong>Location:</strong> ${location}</p>

    <p><a href="${sourceUrl}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />

    <h2>Source text</h2>
    ${
      sourceText
        ? `<pre class="source-text">${escapeHtml(sourceText)}</pre>`
        : `<p class="muted">No source text cached yet for this event. (It may appear after a future daily ingest.)</p>`
    }
    <p class="muted">Source: ${escapeHtml(source?.name || event.sourceId || "Unknown")}</p>
  `;
}

function init() {
  const params = new URLSearchParams(window.location.search);
  const eventId = params.get("id");
  const stored = sessionStorage.getItem("usChessSelectedTournament");

  if (!stored) return missingView();

  try {
    const event = JSON.parse(stored);
    if (!eventId || event.id !== eventId) return missingView();
    renderTournament(event);
  } catch {
    missingView();
  }
}

init();
