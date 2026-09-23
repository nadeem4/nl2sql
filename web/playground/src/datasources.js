// The databases the demo registered, and the one that answered a question.
//
// The demo ships three, and until now the page only ever showed one of them:
// the schema panel opened on the demo's own dataset and nothing led anywhere
// else, although the guided questions named all three. `/api/meta` reports the
// list; the rail turns it into a choice, and `/api/schema?datasource=` serves
// whichever one is picked.
//
// A server with one datasource is left exactly as it was: one name is not a
// choice, so the rail prints no switcher and no run says where it came from.

// Every datasource `/api/meta` reports, in the order it reports them (the one
// the schema panel opens on leads). A server that reports none -- an older
// one, or a page loaded before `/api/meta` answered -- is one database's
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
