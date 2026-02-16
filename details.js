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

function line(label, value) {
  const v = String(value || "").trim();
  if (!v) return "";
  return `<p><strong>${label}:</strong> ${v}</p>`;
}

function cleanVenue(venue, city, state) {
  let v = String(venue || "").trim();
  if (!v) return "";

  const loc = `${city}, ${state}`;
  // Remove redundant city/state embedded in venue
  v = v.replace(loc, "").trim();

  // Clean leftover punctuation
  v = v.replace(/^[,\s-]+/, "").replace(/[,\s-]+$/, "").trim();
  return v;
}

function renderTournament(event) {
  const source = resolveSource(event.sourceId);

  const city = event.city || "Unknown";
  const state = event.state || "US";
  const locationText = `${city}, ${state}`;

  const venueText = cleanVenue(event.venue, city, state);

  const sectionsText =
    Array.isArray(event.sections) && event.sections.length ? event.sections.join(", ") : "";

  detailsRoot.innerHTML = `
    <h1>${event.name}</h1>

    ${line("Dates", formatDateRange(event.startDate, event.endDate))}
    ${line("Location", locationText)}
    ${venueText ? line("Venue", venueText) : ""}

    ${line("Time control", event.timeControl)}
    ${line("Sections", sectionsText)}
    ${line("Entry fee", event.entryFee)}

    ${line("Source", source?.name || "Unknown source")}
    <p><a href="${event.sourceUrl}" target="_blank" rel="noopener noreferrer">Open official listing</a></p>
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
