import { test } from "node:test";
import assert from "node:assert/strict";
import { jobState } from "./jobProgress.js";

const at = "2026-01-01T00:00:00Z";

test("a queued job has no step and no fraction", () => {
  assert.deepEqual(jobState({ status: "queued", progress: [] }), {
    status: "queued", label: "Waiting to start", step: null, done: 0, total: null, fraction: null, over: false,
  });
});

test("a running job shows its latest step and how far it is", () => {
  const job = { status: "running", progress: [
    { at, step: "case 1 of 4", done: 1, total: 4 }, { at, step: "case 2 of 4", done: 2, total: 4 },
  ] };
  const state = jobState(job);
  assert.equal(state.step, "case 2 of 4");
  assert.equal(state.fraction, 0.5);
  assert.equal(state.label, "Running: case 2 of 4");
  assert.equal(state.over, false);
});

test("a step with no total has a count but no fraction", () => {
  const state = jobState({ status: "running", progress: [{ at, step: "chinook", done: 3, total: null }] });
  assert.equal(state.done, 3);
  assert.equal(state.fraction, null);
});

test("a finished job is over, and a failed one says why in the server's words", () => {
  assert.deepEqual(
    [jobState({ status: "succeeded", progress: [] }).label, jobState({ status: "succeeded", progress: [] }).fraction],
    ["Done", 1],
  );
  const failed = jobState({ status: "failed", progress: [], error: { code: "X", message: "No such table." } });
  assert.equal(failed.label, "Failed: No such table.");
  assert.equal(failed.over, true);
  assert.equal(jobState({ status: "cancelled", progress: [] }).label, "Cancelled");
});
