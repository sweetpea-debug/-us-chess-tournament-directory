// details.js
import { formatDateRange } from "./utils.js";

const detailsRoot = document.getElementById("details");

function missingView() {
  detailsRoot.innerHTML = `
    <h1>Tournament not found</h1>
    <p class="muted">Return to the main page and open a tournament card again.</p>
  `;
}

function line(label, value) {
  if (!value) return "";
  return `<p><strong>${label}:</strong> ${value}</p>`;
}

function renderTournament(event) {
  const sectionsText =
    Array.isArray(event.sections) && event.sections.length
      ? event.sections.join(", ")
      : "";

  detailsRoot.innerHTML = `
    <h1>${event.name || "Tournament"}</h1>

    ${line("Dates", formatDateRange(event.startDate, event.endDate))}
    ${line("Location", event.city && event.state ? `${event.city}, ${event.state}` : "")}
    ${line("Venue", event.venue)}

    ${line("Time control", event.timeControl)}
    ${line("Sections", sectionsText)}
    ${line("Entry fee", event.entryFee)}

    ${
      event.sourceUrl
        ? `<p><a href="${event.sourceUrl}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>`
        : ""
    }
  `;
}

function init() {
  const params = new URLSearchParams(window.location.search);
  const eventId = params.get("id");
  const stored = sessionStorage.getItem("usChessSelectedTournament");

  if (!stored) return missingView();

  try {
    const event = JSON.parse(stored);
    if (!eventId || !event?.id || event.id !== eventId) return missingView();
    renderTournament(event);
  } catch {
    missingView();
  }
}

init();
