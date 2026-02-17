export const CACHE_KEY = "us-chess-radar-events-v1";
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;

export function formatDateRange(start, end) {
  if (!start) return "";

  const options = { month: "short", day: "numeric", year: "numeric" };
  const startLabel = new Date(start).toLocaleDateString(undefined, options);

  if (!end || start === end) {
    return startLabel;
  }

  const endLabel = new Date(end).toLocaleDateString(undefined, options);
  return `${startLabel} – ${endLabel}`;
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
  return [...new Set((events || []).map((event) => event.state).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b)
  );
}
