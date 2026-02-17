// Minimal client-side catalog (used only for display / debugging).
// The ingest script controls what sources are actually pulled.

export const SOURCE_CATALOG = [
  {
    id: "uschess-upcoming",
    name: "US Chess",
    homepage: "https://new.uschess.org/upcoming-tournaments",
    category: "federation",
  },
  {
    id: "michess",
    name: "Michigan Chess Association",
    homepage: "https://www.michess.org/events",
    category: "affiliate",
  },
];

// Used only if events.json cannot be loaded.
export const FALLBACK_EVENTS = [];
