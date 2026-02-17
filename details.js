import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  const safeName = event.name || "Untitled event";
  const dates = formatDateRange(event.startDate, event.endDate);
  const location = `${event.city || "Unknown"}, ${event.state || "US"}`;
  const url = event.sourceUrl || "#";

  detailsRoot.innerHTML = `
    <h1>${safeName}</h1>

    <p><strong>Dates:</strong> ${dates}</p>
    <p><strong>Location:</strong> ${location}</p>

    <p><a href="${url}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />
    <h2>Source text</h2>
    <pre class="source-text" id="source-pre"></pre>
  `;

  const pre = document.getElementById("source-pre");
  pre.textContent = event.sourceText || "No source text captured for this event.";
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
