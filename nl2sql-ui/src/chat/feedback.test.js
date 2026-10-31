// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { NOTE_CHOICES, NOTE_MAX, canRate, feedbackBody, ratedLine } from "./feedback.js";

const RESULT = { trace_id: "0b8f7d2e-1111-4222-8333-944455556666", status: "success", replay_miss: false };

test("a run can be rated once it has come back with a trace id", () => {
  assert.equal(canRate(RESULT, false), true);
  assert.equal(canRate(RESULT, true), false);
  assert.equal(canRate(null, false), false);
  assert.equal(canRate({ ...RESULT, trace_id: "" }, false), false);
});

test("a replay miss answered nothing, so there is nothing to rate", () => {
  assert.equal(canRate({ ...RESULT, replay_miss: true }, false), false);
});

test("the body carries only the trace id, the rating and a trimmed note", () => {
  assert.deepEqual(feedbackBody(RESULT, "up", "  "), { trace_id: RESULT.trace_id, rating: "up", note: null });
  assert.deepEqual(feedbackBody(RESULT, "down", " wrong number "),
    { trace_id: RESULT.trace_id, rating: "down", note: "wrong number" });
});

test("a long note is cut to what the server accepts", () => {
  assert.equal(feedbackBody(RESULT, "down", "x".repeat(400)).note.length, NOTE_MAX);
});

test("a rating is up or down, nothing else", () => {
  assert.throws(() => feedbackBody(RESULT, "meh", ""));
});

test("the quick notes are the plain reasons", () => {
  assert.deepEqual(NOTE_CHOICES, ["Wrong number", "Wrong table", "Wrong filter", "Missing rows"]);
});

test("the saved line says what was recorded", () => {
  assert.equal(ratedLine("up", null), "Saved: good answer.");
  assert.equal(ratedLine("down", "wrong table"), "Saved: wrong answer, with the note “wrong table”.");
});
