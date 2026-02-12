## `details.js`
```js
import { SOURCE_CATALOG } from "./data.js";
import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function resolveSource(sourceId) {
  return SOURCE_CATALOG.find((source) => source.id === sourceId);
}

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function renderTournament(event) {
  const source = resolveSource(event.sourceId);
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
    ${source ? `<p><a href="${source.homepage}" target="_blank" rel="noopener noreferrer">Visit source homepage</a></p>` : ""}
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
```
