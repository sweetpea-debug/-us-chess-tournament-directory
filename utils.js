export const CACHE_KEY = "us-chess-radar-events-v2";
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;

export function formatDateRange(startDate, endDate) {
  const options = { month: "short", day: "numeric", year: "numeric" };
  const start = new Date(startDate).toLocaleDateString(undefined, options);
  const end = new Date(endDate).toLocaleDateString(undefined, options);

  // If same day, show just one date
  if (startDate === endDate) return start;
  return `${start} - ${end}`;
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
  return [...new Set(events.map((e) => e.state))].sort((a, b) => a.localeCompare(b));
}
