// utils.js

export function formatDateRange(startISO, endISO) {
  if (!startISO) return "";
  const start = new Date(startISO);
  const end = endISO ? new Date(endISO) : start;

  const sameDay =
    start.getFullYear() === end.getFullYear() &&
    start.getMonth() === end.getMonth() &&
    start.getDate() === end.getDate();

  const fmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

  if (sameDay) return fmt.format(start);
  return `${fmt.format(start)} – ${fmt.format(end)}`;
}

export function stateList(events) {
  const states = new Set();
  for (const e of events || []) {
    if (e?.state) states.add(e.state);
  }
  return Array.from(states).sort();
}

export function escapeHtml(text) {
  const s = String(text ?? "");
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
