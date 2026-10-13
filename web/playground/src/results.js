// The Rows station over a run's saved results, as plain data: the tabs over
// `result_manifest`, the page it asks the server for, the range it says, the
// cut notice and the CSV link. Also the heads the Plan and SQL stations put
// over each sub-query when there are several. No React here, so `npm test`
// covers it with node's own test runner.

// Rows per page: enough to read, small enough that a page arrives at once.
export const PAGE_SIZE = 100;

const n = (value) => Number(value).toLocaleString("en-US");
const rows = (count) => `${n(count)} ${count === 1 ? "row" : "rows"}`;
const base = (requestId, resultId) =>
  `/api/query/${encodeURIComponent(requestId)}/results/${encodeURIComponent(resultId)}`;

// One tab per result, and none at all for a single one: a lone tab says nothing.
// Tabs are numbered; the node id is internal, so it is only the tooltip.
export function resultTabs(manifest) {
  const list = manifest || [];
  if (list.length < 2) return [];
  return list.map((r, i) => ({ id: r.node_id, label: `Result ${i + 1}`, rows: rows(r.row_count), title: r.node_id }));
}

// Which result is shown and where its page starts. Picking a tab starts that
// result at its first page; paging moves within the result on screen.
export function initialView(manifest) {
  const first = (manifest || [])[0];
  return { active: first ? first.node_id : null, offset: 0 };
}

export function rowsView(view, action) {
  if (action.type === "select") return { active: action.id, offset: 0 };
  if (action.type === "page") return { ...view, offset: action.offset };
  return view;
}

export function viewUrl(requestId, view, limit = PAGE_SIZE) {
  return pageUrl(requestId, view.active, view.offset, limit);
}

export function pageUrl(requestId, resultId, offset, limit = PAGE_SIZE) {
  return `${base(requestId, resultId)}?offset=${offset}&limit=${limit}`;
}

// The whole result, streamed by the server; never the sample the page holds.
export function csvUrl(requestId, resultId) {
  return `${base(requestId, resultId)}.csv`;
}

// Where Previous and Next go, or null where there is nowhere to go. Next is
// the server's own `next_offset`, so the last page is the server's call.
export function pageNav(page) {
  const prev = page.offset > 0 ? Math.max(0, page.offset - page.limit) : null;
  const next = page.next_offset === undefined ? null : page.next_offset;
  return { prev, next };
}

// "Rows 101–200 of 3,340", out of the selected result's own row_count, the
// same number its tab and head show.
export function pageRange(page, summary) {
  const shown = (page.rows || []).length;
  if (!shown) return "No rows";
  const total = summary.row_count;
  if (total === 1) return "Row 1 of 1";
  return `Rows ${n(page.offset + 1)}–${n(page.offset + shown)} of ${n(total)}`;
}

export function rowCount(summary) {
  return rows(summary.row_count);
}

// A result with no rows is shown from the manifest alone; any other result
// starts with a request for its first page (null here).
export function firstPage(summary) {
  if (summary.row_count > 0) return null;
  return { columns: summary.columns || [], rows: [], offset: 0, limit: PAGE_SIZE, total_rows: 0, next_offset: null };
}

// A result cut at its row ceiling says so, and what to do about it. The
// ceiling is the sub-query's own row cap when the run reports one.
export function truncatedNotice(summary, subQueries) {
  if (!summary.truncated) return null;
  const capped = (subQueries || []).find((s) => s.truncated && s.row_cap);
  const cap = capped ? capped.row_cap : summary.row_count;
  return `Cut at ${n(cap)} rows, so some are missing. Narrow the question to get them all.`;
}

// With several sub-queries the Plan and SQL stations show each under its own
// head; with one there is nothing to tell apart.
export function subQueryHeads(subs) {
  const list = subs || [];
  if (list.length < 2) return [];
  return list.map((s, i) => ({
    key: s.id || String(i),
    title: `Sub-query ${i + 1} of ${list.length}`,
    detail: [s.intent, s.datasource_id].filter(Boolean).join(" · "),
  }));
}

// A verified answer's badge: the query's id, and the values that were bound,
// for the tooltip and the Plan station. Null for a generated answer, so it
// shows nothing new.
export function verifiedSource(result) {
  const source = result && result.source;
  if (!source || source.type !== "verified" || !source.id) return null;
  const params = Object.entries(source.params || {}).map(([name, value]) => ({ name, value: String(value) }));
  const qualified = source.datasource_id ? `${source.datasource_id}.${source.id}` : source.id;
  const values = params.length ? ` Values: ${params.map((p) => `${p.name} = ${p.value}`).join(", ")}.` : "";
  return {
    id: source.id,
    label: `Verified · ${source.id}`,
    title: `Answered by the verified query ${qualified}, SQL your data team wrote and reviewed.${values}`,
    params,
  };
}
