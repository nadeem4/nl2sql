// A dashboard filter's chosen values and the body of a refresh that carries them. A refresh with filters returns its
// result and stores nothing, so the selection lives only on the page.
// `in` filters take a list of a dimension's values; `between` takes [from, to] of a temporal or number column.

export function setValues(selection, id, values) {
  const next = { ...selection };
  if (!values || values.length === 0) delete next[id];
  else next[id] = values;
  return next;
}

export function isComplete(filter, values) {
  if (!Array.isArray(values)) return false;
  if (filter.op === "between") return values.length === 2 && values.every((v) => v !== "" && v !== null && v !== undefined);
  return values.length > 0;
}

export const inputType = (filter) => (filter.kind === "temporal" ? "date" : filter.kind === "number" ? "number" : "text");

const typed = (filter, values) =>
  filter.op === "between" && filter.kind === "number" ? values.map(Number) : values;

// filter id -> values, for the filters that are complete.
export function filtersBody(filters, selection) {
  const body = {};
  for (const filter of filters) {
    const values = selection[filter.id];
    if (isComplete(filter, values)) body[filter.id] = typed(filter, values);
  }
  return body;
}

export const activeCount = (filters, selection) => Object.keys(filtersBody(filters, selection)).length;

// The body of POST /dashboards/{id}/refresh: one tile or all, the filters to apply, the approvals granted.
export function refreshBody({ tileId, filters = [], selection = {}, approvals = [] }) {
  const body = {};
  if (tileId) body.tile_id = tileId;
  const chosen = filtersBody(filters, selection);
  if (Object.keys(chosen).length) body.filters = chosen;
  if (approvals.length) body.approval_ids = approvals;
  return body;
}
