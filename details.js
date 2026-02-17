import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function missingView(message = "Tournament not found") {
  detailsRoot.innerHTML = `
    <h1>${message}</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  const dates = formatDateRange(event.startDate, event.endDate);
  const location = `${event.city || "Unknown"}, ${event.state || "US"}`;

  const sourceText = typeof event.sourceText === "string" ? event.sourceText : "";

  detailsRoot.innerHTML = `
    <h1>${event.name || "Untitled event"}</h1>

    <p><strong>Dates:</strong> ${dates}</p>
    <p><strong>Location:</strong> ${location}</p>

    <p><a href="${event.sourceUrl}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />

    <h2>Source text</h2>
    <pre class="source-text"></pre>
  `;

  const pre = detailsRoot.querySelector(".source-text");
  if (pre) {
    pre.textContent = sourceText || "No source text was captured for this event yet.";
  }
}

function init() {
  if (!detailsRoot) return;

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
    missingView("Tournament could not be loaded");
  }
}

init();
