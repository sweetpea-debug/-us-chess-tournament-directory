export function formatDateRange(startIso, endIso) {
  if (!startIso) return "";
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : start;

  const opts = { year: "numeric", month: "short", day: "numeric" };
  const startText = start.toLocaleDateString(undefined, opts);
  const endText = end.toLocaleDateString(undefined, opts);

  return startIso === endIso ? startText : `${startText} - ${endText}`;
}

export function stateList(events) {
  const set = new Set();
  (events || []).forEach((e) => {
    if (e?.state) set.add(e.state);
  });
  return Array.from(set).sort();
}
