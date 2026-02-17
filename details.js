import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function esc(str) {
  return String(str ?? "");
}

function renderTournament(event) {
  const sourceText = esc(event.sourceText || "").trim();

  detailsRoot.innerHTML = `
    <h1>${esc(event.name || "Untitled event")}</h1>

    <p><strong>Dates:</strong> ${formatDateRange(event.startDate, event.endDate)}</p>
    <p><strong>Location:</strong> ${esc(event.city || "Unknown")}, ${esc(event.state || "US")}</p>

    <p><a href="${esc(event.sourceUrl || "#")}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />

    <h2>Source text</h2>
    <pre class="source-pre" id="source-pre"></pre>
  `;

  const pre = document.getElementById("source-pre");
  if (pre) {
    pre.textContent = sourceText || "No source text was captured for this event.";
  }
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
