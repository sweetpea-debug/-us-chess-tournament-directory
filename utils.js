export const CACHE_KEY = "us-chess-radar-events-v2";
export const CITY_KEY = "us-chess-radar-city-v1";
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
export const SEARCH_RADIUS_MILES = 100;

function sameDayISO(startISO, endISO) {
  if (!startISO || !endISO) return false;
  return String(startISO).slice(0, 10) === String(endISO).slice(0, 10);
}

export function formatDateRange(startDate, endDate) {
  if (!startDate) return "";
  const options = { month: "short", day: "numeric", year: "numeric" };
  const start = new Date(startDate).toLocaleDateString(undefined, options);

  if (!endDate || sameDayISO(startDate, endDate)) {
    return start;
  }

  const end = new Date(endDate).toLocaleDateString(undefined, options);
  return `${start} - ${end}`;
}

export function haversineMiles(lat1, lon1, lat2, lon2) {
  const a = Number(lat1);
  const b = Number(lon1);
  const c = Number(lat2);
  const d = Number(lon2);
  if (![a, b, c, d].every((x) => Number.isFinite(x))) return null;

  const toRad = (deg) => (deg * Math.PI) / 180;
  const R = 3958.8;

  const dLat = toRad(c - a);
  const dLon = toRad(d - b);

  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a)) * Math.cos(toRad(c)) * Math.sin(dLon / 2) ** 2;

  return 2 * R * Math.asin(Math.sqrt(h));
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
  return [...new Set(events.map((event) => event.state).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b),
  );
}
