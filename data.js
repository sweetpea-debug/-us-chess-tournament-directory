import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  detailsRoot.innerHTML = `
    <h1>${escapeHtml(event.name || "Tournament")}</h1>

    <p><strong>Dates:</strong> ${escapeHtml(formatDateRange(event.startDate, event.endDate))}</p>
    <p><strong>Location:</strong> ${escapeHtml(`${event.city || "Unknown"}, ${event.state || "US"}`)}</p>

    <p><a href="${escapeAttr(event.sourceUrl || "#")}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>

    <hr />

    <h2>Source text</h2>
    <pre class="source-pre" id="source-pre"></pre>
  `;

  const pre = document.getElementById("source-pre");
  pre.textContent = event.sourceText || "(No source text available for this event.)";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  // minimal attr escaping
  return String(value ?? "").replaceAll('"', "%22");
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
