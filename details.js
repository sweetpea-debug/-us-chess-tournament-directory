import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  const sourceText = event.sourceText ? escapeHtml(event.sourceText) : "No source text available.";

  detailsRoot.innerHTML = `
    <h1>${escapeHtml(event.name)}</h1>

    <p><strong>Dates:</strong> ${escapeHtml(formatDateRange(event.startDate, event.endDate))}</p>
    <p><strong>Location:</strong> ${escapeHtml(`${event.city}, ${event.state}`)}</p>

    <p><a href="${escapeHtml(event.sourceUrl)}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr class="rule" />

    <h2>Source text</h2>
    <pre class="source-text">${sourceText}</pre>
  `;
}

function init() {
  const params = new URLSearchParams(window.location.search);
  const eventId = params.get("id");
  const stored = sessionStorage.getItem("usChessSelectedTournament");

  if (!stored) {
    missingView();
    return;
  }

  try {
    const event = JSON.parse(stored);
    if (!eventId || event.id !== eventId) {
      missingView();
      return;
    }
    renderTournament(event);
  } catch {
    missingView();
  }
}

init();
