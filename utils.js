export const CACHE_KEY = "us-chess-radar-events-v1";
export const CITY_KEY = "us-chess-radar-city-v1";
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
export const SEARCH_RADIUS_MILES = 100;

export function formatDateRange(startDate, endDate) {
  const options = { month: "short", day: "numeric", year: "numeric" };
  const start = new Date(startDate).toLocaleDateString(undefined, options);
  const end = new Date(endDate).toLocaleDateString(undefined, options);
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
  if (!raw) {
    return null;
  }

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
