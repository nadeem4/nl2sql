// Pure helpers over one retrieval record: an MMR search's pool, picks and
// scores, as the run trace and `POST /api/retrieval` return it. No React here,
// so `npm test` covers them with node's own test runner.

const TYPE_NAMES = {
  "schema.datasource": "datasource",
  "schema.table": "table",
  "schema.column": "column",
  "schema.relationship": "join",
  "schema.metric": "metric",
};

export function shortType(type) {
  return TYPE_NAMES[type] || (type || "").replace(/^schema\./, "");
}

// What each engine search looks for, in the words the drill-down uses.
const SEARCH_NAMES = {
  datasources: "Datasource search",
  tables: "Table search",
  columns: "Column search",
  planning: "Columns and joins of the picked tables",
};

export function searchTitle(search) {
  return SEARCH_NAMES[search && search.search] || "Search";
}

// A score to three places; a withheld or missing one is a dash.
export function score(value) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

// The picks in the order MMR made them.
export function picksInOrder(search) {
  return ((search && search.pool) || [])
    .filter((e) => e.picked)
    .sort((a, b) => a.pick_order - b.pick_order);
}

// Entries MMR passed over although they are closer to the query than a pick:
// the re-ordering it did, because each repeats an earlier pick.
export function passedOver(search) {
  const pool = (search && search.pool) || [];
  const lastPick = Math.max(0, ...pool.filter((e) => e.picked).map((e) => e.rank));
  return pool.filter((e) => !e.picked && e.rank < lastPick);
}

// One sentence on how the picks were made, with this search's numbers.
export function mmrLine(search) {
  if (!search) return "";
  const pool = (search.pool || []).length;
  const picks = (search.picks || []).length;
  const lam = Number(search.lambda_mult);
  const rest = Math.round((1 - lam) * 100) / 100;
  if (!pool) return "Nothing in the index matched the filter.";
  const head = `Picked ${picks} of the ${pool} nearest ${pool === 1 ? "entry" : "entries"} (k ${search.k}, pool up to ${search.fetch_k}).`;
  if (picks <= 1) return head;
  if (lam >= 1) return `${head} With lambda 1 the picks are simply the most similar.`;
  return `${head} The first pick is the most similar; each later one scores ${lam} x similarity - ${rest} x its similarity to the closest earlier pick.`;
}

// Entry types present in a pool, with counts, for a one-line summary.
export function typeCounts(pool) {
  const counts = {};
  for (const e of pool || []) {
    const t = shortType(e.type);
    counts[t] = (counts[t] || 0) + 1;
  }
  return Object.entries(counts).map(([type, count]) => ({ type, count }));
}

// The search as fixed-width plain text, so two runs (before and after a
// chunking change) can be diffed line by line.
export function asText(search) {
  if (!search) return "";
  const head = [
    `query: ${search.query}`,
    `k ${search.k}  fetch_k ${search.fetch_k}  lambda ${search.lambda_mult}`,
  ];
  const f = search.filter || {};
  if (f.datasource_id || (f.types && f.types.length)) {
    head.push(`filter: ${[f.datasource_id, ...(f.types || []).map(shortType)].filter(Boolean).join(" ")}`);
  }
  const rows = (search.pool || []).map((e) => [
    String(e.rank).padStart(3),
    (e.picked ? `pick ${e.pick_order}` : "drop").padEnd(7),
    score(e.similarity).padStart(6),
    score(e.mmr_score).padStart(6),
    shortType(e.type).padEnd(10),
    e.label || e.id,
  ].join("  "));
  return [...head, "", "  #  result     sim     mmr  type        entry", ...rows].join("\n");
}
