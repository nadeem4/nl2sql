// Pure helpers for the Pipeline page. The steps themselves come from the
// server (GET /api/pipeline), which reads them from `nl2sql.pipeline.steps`;
// nothing here names a node, a model or a provider.
//
// The page joins that list to the last run's telemetry by node name, which is
// the same name `usage.nodes` and `timings` are keyed by, so the numbers on a
// step are that step's own.

// One row per described step, in the described order, carrying whatever the
// last run recorded for it. Before any run every row is blank, which is what
// makes the page useful on arrival.
export function pipelineRows(steps, result) {
  const usage = (result && result.usage) || null;
  const nodes = (usage && usage.nodes) || {};
  const times = (result && result.timings) || {};
  return (steps || []).map((step) => {
    const u = nodes[step.node] || null;
    const seconds = times[step.node];
    return {
      ...step,
      depth: step.parent ? 1 : 0,
      usage: u,
      seconds,
      ran: Boolean(u) || seconds !== undefined,
      retried: Boolean(u && u.calls > 1),
    };
  });
}

// The model a run actually called for one step, as its calls reported it. A
// step served by two models (a retry after a settings change) names both.
export function modelUsed(usage, node) {
  const models = [];
  for (const call of (usage && usage.calls) || []) {
    if (call.node === node && call.model && !models.includes(call.model)) models.push(call.model);
  }
  return models.length ? models.join(", ") : null;
}

export function countModelSteps(steps) {
  return (steps || []).filter((step) => step.kind === "model").length;
}
