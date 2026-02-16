export function formatDateRange(startISO, endISO) {
  if (!startISO) return "";
  const start = new Date(startISO);
  const end = endISO ? new Date(endISO) : start;

  const sameDay =
    start.getFullYear() === end.getFullYear() &&
    start.getMonth() === end.getMonth() &&
    start.getDate() === end.getDate();

  const fmt = (d) =>
    d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

  return sameDay ? fmt(start) : `${fmt(start)} - ${fmt(end)}`;
}

export function stateList(events) {
  const set = new Set();
  (events || []).forEach((e) => {
    if (e && e.state) set.add(e.state);
  });
  return Array.from(set).sort();
}
