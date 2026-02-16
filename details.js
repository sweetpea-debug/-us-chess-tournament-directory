import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderTournament(event) {
  const fullText = event.fullText ? escapeHtml(event.fullText) : "No source text captured.";

  detailsRoot.innerHTML = `
    <h1>${escapeHtml(event.name)}</h1>
    <p><strong>Dates:</strong> ${escapeHtml(formatDateRange(event.startDate, event.endDate))}</p>
    <p><strong>Location:</strong> ${escapeHtml(`${event.city}, ${event.state}`)}</p>

    <p><a href="${escapeHtml(event.sourceUrl)}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />

    <h2>Source text</h2>
    <pre class="source-text">${fullText}</pre>
  `;
}

function init() {
  const params = new URLSearchParams(window.location.search);
  const eventId = params.get("id");
  const stored = sessionStorage.getItem("selectedTournament");

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
