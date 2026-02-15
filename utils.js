export const CACHE_KEY = "usChessRadarCacheV1";
export const CITY_KEY = "usChessRadarCityV1";

export const CACHE_TTL_MS = 1000 * 60 * 60 * 6; // 6 hours
export const SEARCH_RADIUS_MILES = 100;

export function readStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function writeStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore quota errors
  }
}

export function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateRange(startIso, endIso) {
  const start = formatDate(startIso);
  const end = formatDate(endIso);

  if (!start) return "";
  if (!end) return start;

  // same day -> show single date
  if (String(startIso).slice(0, 10) === String(endIso).slice(0, 10)) {
    return start;
  }

  return `${start} - ${end}`;
}

function toRadians(deg) {
  return (deg * Math.PI) / 180;
}

export function haversineMiles(lat1, lon1, lat2, lon2) {
  const a = Number(lat1);
  const b = Number(lon1);
  const c = Number(lat2);
  const d = Number(lon2);

  if (![a, b, c, d].every((x) => Number.isFinite(x))) return NaN;

  const R = 3958.8; // miles
  const dLat = toRadians(c - a);
  const dLon = toRadians(d - b);

  const s1 = Math.sin(dLat / 2) ** 2;
  const s2 = Math.cos(toRadians(a)) * Math.cos(toRadians(c)) * Math.sin(dLon / 2) ** 2;

  const h = s1 + s2;
  const dist = 2 * R * Math.asin(Math.sqrt(h));
  return dist;
}

export function stateList(events) {
  const states = new Set();
  (events || []).forEach((e) => {
    if (e && e.state) states.add(e.state);
  });
  return Array.from(states).sort();
}
