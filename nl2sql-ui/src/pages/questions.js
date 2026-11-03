// The guided questions, in piles rather than one list.
//
// The demo registers three databases, so twenty guided questions run together
// unless each pile says which database it asks about. `GET /meta` lists each
// datasource the caller may reach with its `sample_questions`; one pile per
// datasource, and none for a datasource that has no questions.

export function guidedGroups(meta) {
  const sources = meta && Array.isArray(meta.datasources) ? meta.datasources : [];
  return sources
    .filter((d) => d && d.sample_questions && d.sample_questions.length)
    .map((d) => ({ datasource: d.id, questions: d.sample_questions }));
}
