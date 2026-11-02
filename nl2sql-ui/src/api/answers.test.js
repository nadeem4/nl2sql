import { test } from "node:test";
import assert from "node:assert/strict";
import { deleteAnswer, listAnswers, runAnswer, saveAnswer } from "./answers.js";
import { fakeApi } from "../dashboards/fakeApi.js";

test("saving sends the run and the step, and answers the report", async () => {
  const calls = fakeApi({ answer: { id: "a1" }, proposed: true });
  const body = { trace_id: "t", conversation_id: "c", step: { turn: 2, step: 1 } };
  assert.deepEqual(await saveAnswer(body), { answer: { id: "a1" }, proposed: true });
  assert.deepEqual(calls, [{ method: "POST", path: "/answers", body }]);
});

test("the list is the caller's, or one datasource's", async () => {
  const calls = fakeApi([]);
  await listAnswers();
  await listAnswers("pagila db");
  assert.deepEqual(calls.map((c) => [c.method, c.path]), [["GET", "/answers"], ["GET", "/answers?datasource_id=pagila%20db"]]);
});

test("a delete answers nothing", async () => {
  const calls = fakeApi({});
  await deleteAnswer("a/1");
  assert.deepEqual(calls.map((c) => [c.method, c.path]), [["DELETE", "/answers/a%2F1"]]);
});

test("a run carries the approvals only when there are some", async () => {
  const calls = fakeApi({ status: "success" });
  await runAnswer("a1");
  await runAnswer("a1", ["r1"]);
  assert.deepEqual(calls.map((c) => c.body), [undefined, { approval_ids: ["r1"] }]);
  assert.equal(calls[0].path, "/answers/a1/run");
});
