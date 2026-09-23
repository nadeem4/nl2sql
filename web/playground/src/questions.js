// The guided questions, in piles rather than one list.
//
// The demo registers three databases, so twenty guided questions run together
// unless each pile says which database it asks about. `/api/meta` groups them
// (`question_groups`); a server that sends none is one database's worth, and
// the page then shows the flat list under the dataset and prints no label.

export function guidedGroups(meta) {
  if (!meta) return [];
  const groups = Array.isArray(meta.question_groups) ? meta.question_groups : [];
  const source = groups.length ? groups : [{ datasource: meta.dataset, questions: meta.questions }];
  return source
    .filter((group) => group && group.questions && group.questions.length)
    .map((group) => ({ datasource: group.datasource, questions: group.questions }));
}
