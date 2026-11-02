// Run with `npm test`. What a turn's stream adds up to: Wave A's event names fill the spine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { EMPTY_SPINE, liveStations, spineOf } from "./spine.js";

// A frame as http.stream hands it over: the event name and the whole Event as JSON.
const frame = (event, data = {}, trace_id = "t-1") => ({ event, data: { name: event, trace_id, data } });
const run = (...frames) => frames.reduce(spineOf, EMPTY_SPINE);

test("node.started marks the live step and node.finished completes it with its time", () => {
  let spine = run(frame("node.started", { node: "ast_planner" }));
  assert.equal(spine.live, "ast_planner");
  spine = spineOf(spine, frame("node.finished", { node: "ast_planner", seconds: 2.5 }));
  assert.equal(spine.live, null);
  assert.deepEqual(spine.finished, ["ast_planner"]);
  assert.equal(spine.timings.ast_planner, 2.5);
});

test("a step that runs twice keeps its longest time and is listed once", () => {
  const spine = run(
    frame("node.finished", { node: "ast_planner", seconds: 3 }),
    frame("node.finished", { node: "ast_planner", seconds: 1 }),
  );
  assert.deepEqual(spine.finished, ["ast_planner"]);
  assert.equal(spine.timings.ast_planner, 3);
});

test("result.manifest keeps the run's trace id and makes the rows readable before the answer", () => {
  const spine = run(frame("result.manifest", { results: [{ id: "r1" }] }, "t-9"));
  assert.equal(spine.traceId, "t-9");
  assert.deepEqual(spine.manifest, [{ id: "r1" }]);
});

test("answer.delta grows the summary and ignores a field it does not show", () => {
  const spine = run(
    frame("answer.delta", { field: "summary", text: "Rock " }),
    frame("answer.delta", { field: "summary", text: "wins." }),
    frame("answer.delta", { field: "other", text: "x" }),
  );
  assert.equal(spine.answer.summary, "Rock wins.");
});

test("a second question in the turn starts the steps again but keeps the tool steps", () => {
  const spine = run(
    frame("node.finished", { node: "ast_planner", seconds: 1 }),
    frame("conversation.step", { tool: "ask", status: "ok", trace_id: "t-1" }),
    frame("run.started", {}, "t-2"),
  );
  assert.deepEqual(spine.finished, []);
  assert.equal(spine.answer.summary, "");
  assert.deepEqual(spine.steps, [{ tool: "ask", status: "ok", traceId: "t-1" }]);
});

test("conversation.turn carries the stored turn and run.failed the reason", () => {
  const turn = { index: 1, status: "answered" };
  assert.deepEqual(run(frame("conversation.turn", { stored: turn })).turn, turn);
  assert.equal(run(frame("run.failed", { message: "No key." })).failed, "No key.");
});

test("an event the spine does not read changes nothing", () => {
  assert.equal(spineOf(EMPTY_SPINE, frame("llm.call", {})), EMPTY_SPINE);
  assert.equal(spineOf(EMPTY_SPINE, { event: "message", data: "text" }), EMPTY_SPINE);
});

test("the stations follow the finished steps: done, then the first waiting one live, the rest queued", () => {
  assert.deepEqual(liveStations({ finished: ["ast_planner"] }), {
    plan: "done", checks: "busy", sql: "queued", rows: "queued", cost: "queued",
  });
  assert.equal(liveStations({ finished: ["ast_planner", "logical_validator", "generator", "executor"] }).cost, "busy");
});
