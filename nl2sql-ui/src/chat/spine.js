// The live turn, as plain data: what a conversation stream's events (Wave A's names, plus `conversation.step` and
// `conversation.turn`) add up to. No React here, so `npm test` covers it with node's own test runner.
//
// `api/http.js` hands each frame over as `{event, data}`, where `data` is the whole Event as JSON: `name`, `at`,
// `trace_id`, `tenant_id` and the event's own `data`. A turn can ask several questions; each one's `run.started`
// starts the step list again, because the spine shows the question that is running.

export const EMPTY_SPINE = Object.freeze({
  live: null,
  finished: [],
  timings: {},
  answer: { summary: "", content: "" },
  manifest: null,
  traceId: null,
  steps: [],
  turn: null,
  failed: null,
});

export function spineOf(spine, frame) {
  const whole = frame && frame.data && typeof frame.data === "object" ? frame.data : null;
  if (!whole) return spine;
  const d = whole.data || {};
  switch (frame.event) {
    case "run.started":
      return { ...EMPTY_SPINE, steps: spine.steps };
    case "node.started":
      return { ...spine, live: d.node };
    case "node.finished": {
      const prior = spine.timings[d.node];
      const seconds = Number(d.seconds) || 0;
      return {
        ...spine,
        live: spine.live === d.node ? null : spine.live,
        finished: spine.finished.includes(d.node) ? spine.finished : [...spine.finished, d.node],
        timings: { ...spine.timings, [d.node]: prior === undefined ? seconds : Math.max(prior, seconds) },
      };
    }
    case "result.manifest":
      return { ...spine, manifest: d.results || [], traceId: whole.trace_id || null };
    case "answer.delta":
      if (!(d.field in spine.answer)) return spine;
      return { ...spine, answer: { ...spine.answer, [d.field]: spine.answer[d.field] + (d.text || "") } };
    case "conversation.step":
      return { ...spine, steps: [...spine.steps, { tool: d.tool, status: d.status, traceId: d.trace_id || null }] };
    case "conversation.turn":
      return { ...spine, turn: d.stored || null };
    case "run.failed":
      return { ...spine, failed: d.message || "The turn failed." };
    default:
      return spine;
  }
}

// The stations, the node each one waits on, in the order a question reaches them.
const STEPS = [
  ["plan", "ast_planner"],
  ["checks", "logical_validator"],
  ["sql", "generator"],
  ["rows", "executor"],
];

// While a question runs: a station whose step finished is done, the first one still waiting is live, the rest are
// queued. Cost & time is the whole run, so it is live once the rows are in and done only when the result is back.
export function liveStations(spine) {
  const states = {};
  let live = false;
  for (const [station, node] of STEPS) {
    if (spine.finished.includes(node)) states[station] = "done";
    else if (!live) {
      states[station] = "busy";
      live = true;
    } else states[station] = "queued";
  }
  states.cost = live ? "queued" : "busy";
  return states;
}
