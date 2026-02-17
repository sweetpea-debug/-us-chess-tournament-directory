// utils.js
export function formatDateRange(startIso, endIso) {
  if (!startIso) return "";
  if (!endIso || endIso === startIso) {
    return new Date(startIso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  const start = new Date(startIso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const end = new Date(endIso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  return `${start} – ${end}`;
}

export function stateList(events) {
  const set = new Set();
  for (const ev of events || []) {
    if (ev?.state) set.add(ev.state);
  }
  return Array.from(set).sort();
}
