// The datasources the server reports, and the one that answered a question.

// Every datasource `/api/meta` reports, in the order it reports them (the first leads). A server that reports none -- an
// older one, or a page loaded before `/api/meta` answered -- is one database's
// worth, named by `dataset`.
export function datasourceNames(meta) {
  if (!meta) return [];
  const names = (Array.isArray(meta.datasources) ? meta.datasources : []).filter(Boolean);
  if (names.length) return names;
  return meta.dataset ? [meta.dataset] : [];
}

// The database (or databases) a run was answered from, as the resolver picked
// it: each sub-query carries the datasource it was planned against. Empty
// until a run comes back, and for a run that stopped before any sub-query.
export function answeredDatasources(result) {
  const subs = (result && result.sub_queries) || [];
  return [...new Set(subs.map((s) => s && s.datasource_id).filter(Boolean))];
}
