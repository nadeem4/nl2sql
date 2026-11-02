// A drift check (`GET /index/check`, a `DriftReport` per datasource) as the Drift panel reads it: one label, the
// changes in words, and what they break. Pure, so `npm test` covers it.

const STATUS = {
  unchanged: { label: "Matches the index", tone: "ok", rank: 3 },
  drifted: { label: "Changed since the index was built", tone: "warn", rank: 0 },
  not_indexed: { label: "Not indexed yet", tone: "warn", rank: 2 },
  failed: { label: "Could not check", tone: "fault", rank: 1 },
};

const table = (ref) => `${ref.schema_name}.${ref.table_name}`;
const column = (ref) => `${table(ref.table)}.${ref.column_name}`;
const foreignKey = (fk) =>
  `${table(fk.table)} (${fk.columns.join(", ")}) to ${table(fk.referred_table)} (${fk.referred_columns.join(", ")})`;
const change = (changes) =>
  Object.entries(changes).map(([what, [from, to]]) => `${what} ${from} to ${to}`).join(", ");

function changesOf(diff) {
  if (!diff) return [];
  return [
    ...diff.tables_added.map((t) => `Added table ${table(t)}`),
    ...diff.tables_dropped.map((t) => `Dropped table ${table(t)}`),
    ...diff.columns_added.map((c) => `Added column ${column(c)}`),
    ...diff.columns_dropped.map((c) => `Dropped column ${column(c)}`),
    ...diff.columns_changed.map((c) => `Changed ${table(c.table)}.${c.column}: ${change(c.changes)}`),
    ...diff.foreign_keys_added.map((fk) => `Added foreign key ${foreignKey(fk)}`),
    ...diff.foreign_keys_dropped.map((fk) => `Dropped foreign key ${foreignKey(fk)}`),
    ...(diff.engine_type ? [`Engine type changed from ${diff.engine_type[0]} to ${diff.engine_type[1]}`] : []),
  ];
}

function impactOf(impact) {
  if (!impact) return [];
  const sections = [
    ["Verified queries", impact.verified_queries.map((q) => `${q.query_id}: ${q.reasons.join("; ")}`)],
    ["Glossary items", impact.glossary_items],
    ["Policy rules", impact.policy_rules.map((r) => `${r.role}: ${r.kind} ${r.pattern}`)],
    ["Preferred tables", impact.preferred_tables],
  ];
  return sections.filter(([, lines]) => lines.length).map(([label, lines]) => ({ label, lines }));
}

export function driftSummary(report) {
  const status = STATUS[report.status] || STATUS.failed;
  return {
    label: status.label,
    tone: status.tone,
    changes: changesOf(report.diff),
    impact: impactOf(report.impact),
    error: report.error || null,
  };
}

// The ones that need a look first (drifted, failed, not indexed), then the quiet ones, each group by name.
export function sortReports(reports) {
  const rank = (r) => (STATUS[r.status] || STATUS.failed).rank;
  return [...reports].sort((a, b) => rank(a) - rank(b) || a.datasource_id.localeCompare(b.datasource_id));
}
