// Pure helpers for the index panel. The server judges health
// (nl2sql/indexing/health.py); these only put its answer into words.

// Entry kinds in the order a reader thinks about a database, with plain names.
const KINDS = [
  ["schema.datasource", "datasource", "datasources"],
  ["schema.table", "table", "tables"],
  ["schema.column", "column", "columns"],
  ["schema.relationship", "relationship", "relationships"],
];

// [{kind, label, count}] for every kind present, known kinds first.
export function countRows(counts) {
  const c = counts || {};
  const rows = KINDS.filter(([kind]) => c[kind] != null).map(([kind, one, many]) => ({
    kind,
    label: c[kind] === 1 ? one : many,
    count: c[kind],
  }));
  const known = new Set(KINDS.map(([k]) => k));
  for (const kind of Object.keys(c).sort()) {
    if (!known.has(kind)) rows.push({ kind, label: kind.replace(/^schema\./, ""), count: c[kind] });
  }
  return rows;
}

// One sentence for the status line.
export function statusLine(health) {
  if (!health) return "Checking the index.";
  switch (health.status) {
    case "ok":
      return `${health.total.toLocaleString()} entries, built from the latest schema.`;
    case "empty":
      return "The index is empty, so every question will fail before it reaches the model.";
    case "missing":
      return "There is no index yet, so every question will fail before it reaches the model.";
    case "stale":
      return "The index is out of date with the schema.";
    default:
      return health.status;
  }
}

export function needsRebuild(health) {
  return Boolean(health) && health.status !== "ok";
}

// "3 minutes ago", "2 days ago"; the exact time goes in a title attribute.
export function relativeTime(iso, now = Date.now()) {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const s = Math.max(0, Math.round((now - then) / 1000));
  const units = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ];
  for (const [size, name] of units) {
    if (s >= size) {
      const n = Math.floor(s / size);
      return `${n} ${name}${n === 1 ? "" : "s"} ago`;
    }
  }
  return "just now";
}

// The schema version as the panel shows it: the build time part is noise next
// to "Built", the hash is what identifies the structure.
export function shortVersion(version) {
  if (!version) return null;
  const hash = version.split("_")[1];
  return hash || version;
}
