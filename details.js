// details.js
import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function escapeHtml(s) {
  return String(s || "")
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
  const dateText = formatDateRange(event.startDate, event.endDate);
  const locationText = `${event.city || "Unknown"}, ${event.state || "US"}`;
  const sourceUrl = event.sourceUrl || "#";
  const sourceText = event.sourceText || "No source text captured for this event yet.";

  detailsRoot.innerHTML = `
    <h1>${escapeHtml(event.name || "Untitled event")}</h1>

    <p><strong>Dates:</strong> ${escapeHtml(dateText)}</p>
    <p><strong>Location:</strong> ${escapeHtml(locationText)}</p>

    <p><a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />

    <h2>Source text</h2>
    <pre style="white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; background: #f7faff; border: 1px solid #bed0ee; padding: 0.9rem; border-radius: 12px;">
${escapeHtml(sourceText)}
    </pre>
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
