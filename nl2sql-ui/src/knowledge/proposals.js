// Proposals as the review list shows them, and the bodies of Accept and Reject (`POST /proposals/accept` and
// `/reject`). Pure, so `npm test` covers it. A proposal's `change` is the fields it would set, in glossary.yaml's or
// curated.yaml's shape; the form edits only its text, and anything structural (a join's ends, a role) is a file edit.

// The kinds in the order a reviewer reads them: what the data means first, then the examples.
const KINDS = [
  ["term", "Terms"],
  ["join", "Joins"],
  ["column", "Columns"],
  ["table", "Tables"],
  ["example", "Examples"],
  ["verified_query", "Verified queries"],
];

export function groupByKind(proposals) {
  const known = KINDS.map(([kind]) => kind);
  const extra = [...new Set(proposals.map((p) => p.kind))].filter((kind) => !known.includes(kind));
  return [...KINDS, ...extra.map((kind) => [kind, kind])]
    .map(([kind, label]) => ({ kind, label, items: proposals.filter((p) => p.kind === kind) }))
    .filter((group) => group.items.length);
}

const joinEnd = (end) => (Array.isArray(end) ? end.join(", ") : String(end ?? ""));

// One line for what the proposal would write.
export function summaryOf(p) {
  const c = p.change || {};
  if (p.kind === "join") {
    const how = c.cardinality && c.cardinality !== "unknown" ? ` (${c.cardinality})` : "";
    return `${joinEnd(c.from)} to ${joinEnd(c.to)}${how}`;
  }
  if (p.kind === "example") return c.question || p.target;
  const said = c.definition || c.description || c.question || c.notes;
  return said ? `${p.target}: ${said}` : p.target;
}

const EDITABLE = ["definition", "description", "notes", "question", "sql", "text"];

// The text fields of the change the edit form offers, in the change's own order.
export function editableFields(p) {
  return Object.entries(p.change || {})
    .filter(([key, value]) => EDITABLE.includes(key) && typeof value === "string")
    .map(([key, value]) => ({ key, value }));
}

const show = (value) => (typeof value === "string" ? value : JSON.stringify(value));

// Counts and checks the engine recorded (never rows), as `key: value` lines.
export function evidenceLines(p) {
  return Object.entries(p.evidence || {}).map(([key, value]) => `${key}: ${show(value)}`);
}

// The body of `POST /proposals/accept`: the ids, and per id only the fields whose text the reviewer changed. A field
// cleared to nothing is not sent; it cannot be removed from a form.
export function acceptBody(ids, drafts, byId) {
  const edits = {};
  for (const id of ids) {
    const original = (byId[id] && byId[id].change) || {};
    for (const [key, raw] of Object.entries((drafts && drafts[id]) || {})) {
      const value = typeof raw === "string" ? raw.trim() : raw;
      if (!value || value === original[key]) continue;
      edits[id] = { ...edits[id], [key]: value };
    }
  }
  return Object.keys(edits).length ? { ids, edits } : { ids };
}

export function rejectBody(ids, reason) {
  const why = (reason || "").trim();
  return why ? { ids, reason: why } : { ids };
}

// What an `AcceptReport` says, for the line under the list.
export function acceptOutcome(report) {
  const accepted = (report.accepted || []).length;
  const files = report.files_written || [];
  let summary = "Nothing was accepted.";
  if (accepted) summary = `Accepted ${accepted}${files.length ? ` and wrote ${files.join(", ")}` : ""}.`;
  return {
    summary,
    refused: Object.entries(report.refused || {}).map(([id, why]) => ({ id, why })),
    warnings: [...(report.warnings || [])],
  };
}
