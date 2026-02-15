export const CACHE_KEY = "us-chess-radar-events-v1";
export const CITY_KEY = "us-chess-radar-city-v1";
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
export const SEARCH_RADIUS_MILES = 100;

export function formatDateRange(startDate, endDate) {
  const options = { month: "short", day: "numeric", year: "numeric" };
  const startIso = String(startDate || "").slice(0, 10);
  const endIso = String(endDate || "").slice(0, 10);

  const start = new Date(startIso).toLocaleDateString(undefined, options);
  const end = new Date(endIso).toLocaleDateString(undefined, options);

  // Single-day event -> show only one date
  if (startIso && endIso && startIso === endIso) {
    return start;
  }

  return `${start} - ${end}`;
}

export function haversineMiles(lat1, lon1, lat2, lon2) {
  const a = Number(lat1);
  const b = Number(lon1);
  const c = Number(lat2);
  const d = Number(lon2);

  if (![a, b, c, d].every((n) => Number.isFinite(n))) {
    return null;
  }

  const toRad = (deg) => (deg * Math.PI) / 180;
  const R = 3958.8;
  const dLat = toRad(c - a);
  const dLon = toRad(d - b);
  const x =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a)) * Math.cos(toRad(c)) * Math.sin(dLon / 2) ** 2;

  return 2 * R * Math.asin(Math.sqrt(x));
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
  return [...new Set((events || []).map((event) => event.state))]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
}
