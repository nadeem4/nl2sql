import { test } from "node:test";
import assert from "node:assert/strict";
import {
  HINT_KEY, answerDate, describeSaveError, hintSeen, markHintSeen, needsFixing, runView, saveBody, savedLine, stepRef,
} from "./answers.js";

const ASK = (result) => ({ call: { name: "ask" }, result });
const TURN = { index: 3, steps: [{ call: { name: "chart" } }, ASK({ trace_id: "a" }), ASK(null), ASK({ trace_id: "b" })] };

test("a step is named by its turn and its place among the turn's answered ask steps", () => {
  assert.deepEqual(stepRef(TURN, TURN.steps[1]), { turn: 3, step: 1 });
  assert.deepEqual(stepRef(TURN, TURN.steps[3]), { turn: 3, step: 2 });
  assert.equal(stepRef(TURN, TURN.steps[0]), null);
});

test("the save body carries the run, the conversation and the step, and a trimmed note only when there is one", () => {
  const ref = { turn: 3, step: 1 };
  assert.deepEqual(saveBody({ trace_id: "t" }, "c1", ref, "  "), { trace_id: "t", conversation_id: "c1", step: ref });
  assert.deepEqual(saveBody({ trace_id: "t" }, "c1", ref, " nice "), { trace_id: "t", conversation_id: "c1", step: ref, note: "nice" });
});

test("the confirmation says where it went, and 'sent for review' only when a proposal was filed", () => {
  assert.equal(savedLine({ proposed: false }), "Saved to My answers.");
  assert.equal(savedLine({ proposed: true, proposal_id: "p1" }), "Saved to My answers and sent for review.");
});

test("a refused save is explained in the asker's words", () => {
  assert.match(describeSaveError({ code: "ANSWER_LIMIT_REACHED", message: "x" }), /My answers is full/);
  assert.match(describeSaveError({ code: "CONFLICT", message: "x" }), /try again/i);
  assert.match(describeSaveError({ code: "NOT_SAVABLE", message: "Step 1 did not answer." }), /Step 1 did not answer\./);
  assert.equal(describeSaveError({ code: "X", message: "plain" }), "plain");
});

test("a run whose first error is TILE_NEEDS_FIXING needs fixing", () => {
  assert.equal(needsFixing({ errors: [{ error_code: "TILE_NEEDS_FIXING" }, { error_code: "Z" }] }), true);
  assert.equal(needsFixing({ errors: [{ error_code: "Z" }, { error_code: "TILE_NEEDS_FIXING" }] }), false);
  assert.equal(needsFixing({ errors: [] }), false);
  assert.equal(needsFixing({}), false);
});

function store(initial) {
  const data = { ...initial };
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = v; }, data };
}

test("the hint shows until the first click, then never again", () => {
  const s = store();
  assert.equal(hintSeen(s), false);
  markHintSeen(s);
  assert.equal(s.data[HINT_KEY], "1");
  assert.equal(hintSeen(s), true);
});

test("storage that is missing or throws never breaks the bar", () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(hintSeen(broken), false);
  assert.doesNotThrow(() => markHintSeen(broken));
  assert.equal(hintSeen(undefined), false);
  assert.doesNotThrow(() => markHintSeen(undefined));
});

test("a run is shown as rows, as needing fixing, as waiting for an approval, or as the error it is", () => {
  assert.deepEqual(runView({ status: "success", result_manifest: [{}] }), { kind: "rows" });
  assert.deepEqual(runView({ status: "error", errors: [{ error_code: "TILE_NEEDS_FIXING", message: "m" }] }),
    { kind: "needs_fixing", message: "This answer needs fixing: the schema has changed since it was saved." });
  assert.deepEqual(runView({ status: "needs_approval", approvals_needed: [{ id: "r1" }, { id: "r2" }] }),
    { kind: "approval", approvalIds: ["r1", "r2"] });
  assert.deepEqual(runView({ status: "error", errors: [{ error_code: "X", message: "boom" }] }), { kind: "error", message: "boom" });
  assert.deepEqual(runView({ status: "error" }), { kind: "error", message: "The answer could not be run." });
});

test("the date of a saved answer is its day, and nothing when it has none", () => {
  assert.equal(answerDate({ created_at: "2026-11-02T10:30:00Z" }), "2026-11-02");
  assert.equal(answerDate({}), "");
});
