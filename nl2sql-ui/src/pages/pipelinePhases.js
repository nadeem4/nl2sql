// The Pipeline page's three phases, each step's state after a run, and the
// waterfall bars. All of it is read from the server's own step list
// (GET /api/pipeline, from `nl2sql.pipeline.steps`) and the last run's
// timings; nothing here invents a step.
//
// The phases are cut where the list itself changes shape: the steps before
// the nested SQL agent understand the question, the SQL agent with its own
// steps answers each sub-query, and the steps after it combine and explain. A
// step the graph gains later lands in a phase by where it sits.

export const PHASES = [
  { id: "understand", title: "Understand the question" },
  { id: "answer", title: "Answer each sub-query",
    note: "repeats per sub-query, and again when the checks refuse a plan" },
  { id: "combine", title: "Combine and explain" },
];

// The loop that hands each layer of sub-queries to the SQL agent. It runs
// once per layer, so it belongs with the answering, not with understanding
// the question. The step list carries no "loop" marker, so it is named here;
// a list without it simply opens the answering phase on the bracket.
const LOOP = "layer_router";

// [{id, title, note, items}], where an item is {type: "step", row} or
// {type: "bracket", parent, rows} for a step and the steps nested under it.
// Phases with nothing in them are left out.
export function pipelinePhases(rows) {
  const list = rows || [];
  const parents = new Set(list.map((r) => r.parent).filter(Boolean));
  const at = list.findIndex((r) => !r.parent && parents.has(r.node));
  const items = { understand: [], answer: [], combine: [] };

  if (at === -1) {
    items.understand = list.map((row) => ({ type: "step", row }));
  } else {
    const parent = list[at];
    const before = list.slice(0, at);
    const loop = before.length && before[before.length - 1].node === LOOP ? before.pop() : null;
    items.understand = before.map((row) => ({ type: "step", row }));
    if (loop) items.answer.push({ type: "step", row: loop });
    items.answer.push({ type: "bracket", parent, rows: list.filter((r) => r.parent === parent.node) });
    items.combine = list.slice(at + 1).filter((r) => r.parent !== parent.node)
      .map((row) => ({ type: "step", row }));
  }

  return PHASES.map((p) => ({ ...p, items: items[p.id] })).filter((p) => p.items.length);
}

// node -> "idle" before any run; after one, "done" for a step that ran,
// "skipped" for one passed over while a later step still ran (a retry that was
// not needed), and "not-run" for one the run never reached.
export function stepStates(rows, ran) {
  const list = rows || [];
  const out = {};
  let laterRan = false;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const row = list[i];
    if (!ran) out[row.node] = "idle";
    else if (row.ran) out[row.node] = "done";
    else out[row.node] = laterRan ? "skipped" : "not-run";
    if (row.ran) laterRan = true;
  }
  return Object.fromEntries(list.map((r) => [r.node, out[r.node]]));
}

// node -> {start, width}, in percent of the run's total time. The timings say
// how long each node took, not when it started, so the bars are laid end to
// end in pipeline order: the nested steps inside their parent's span, the
// steps after it from where that span ends. A node that ran more than once
// (a retry, a second sub-query) reports its longest run, so this is the shape
// of the run rather than a trace of it.
export function waterfall(rows, result) {
  const times = (result && result.timings) || {};
  const list = rows || [];
  const spans = {};
  let cursor = 0;
  let inner = 0;
  for (const row of list) {
    const s = Number(times[row.node]);
    const has = row.ran && Number.isFinite(s) && times[row.node] !== undefined;
    if (row.parent) {
      if (has) {
        spans[row.node] = [inner, s];
        inner += s;
      }
      continue;
    }
    if (has) {
      spans[row.node] = [cursor, s];
      inner = cursor;
      cursor += s;
    } else {
      inner = cursor;
    }
  }
  const ends = Object.values(spans).map(([a, d]) => a + d);
  if (!ends.length) return {};
  const total = Math.max(Number(times.LangGraph) || 0, ...ends);
  if (!(total > 0)) return {};
  const pct = (n) => Math.min(100, (n / total) * 100);
  return Object.fromEntries(Object.entries(spans).map(([node, [a, d]]) => {
    const start = pct(a);
    return [node, { start, width: Math.max(0, Math.min(pct(d), 100 - start)) }];
  }));
}
