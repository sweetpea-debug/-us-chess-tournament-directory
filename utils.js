export function formatDateRange(startDate, endDate) {
  const options = { month: "short", day: "numeric", year: "numeric" };
  const start = new Date(startDate).toLocaleDateString(undefined, options);

  if (!endDate || endDate === startDate) return start;

  const end = new Date(endDate).toLocaleDateString(undefined, options);
  return `${start} - ${end}`;
}

export function stateList(events) {
  return [...new Set(events.map((event) => event.state))]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
}
