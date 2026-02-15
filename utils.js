// utils.js
export const CACHE_KEY = "us-chess-radar-events-v1";
export const CITY_KEY = "us-chess-radar-city-v1";
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
export const SEARCH_RADIUS_MILES = 100;

function toDateOnlyString(value) {
  // value is expected to be YYYY-MM-DD
  if (!value || typeof value !== "string") return "";
  return value.trim().slice(0, 10);
}

export function formatDateRange(startDate, endDate) {
  const startStr = toDateOnlyString(startDate);
  const endStr = toDateOnlyString(endDate) || startStr;

  if (!startStr) return "";

  const options = { month: "short", day: "numeric", year: "numeric" };
  const start = new Date(startStr).toLocaleDateString(undefined, options);

  // If it’s a one-day event, show just the date
  if (startStr === endStr) return start;

  const end = new Date(endStr).toLocaleDateString(undefined, options);
  return `${start} - ${end}`;
}

export function haversineMiles(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const R = 3958.8;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

export function readStorage(key) {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function writeStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

export function stateList(events) {
  return [...new Set(events.map((event) => event.state))].sort((a, b) => a.localeCompare(b));
}
