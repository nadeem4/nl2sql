import { test } from "node:test";
import assert from "node:assert/strict";
import { approvalState, ownRequest, toDecide } from "./approval.js";

const rec = (id, status) => ({ id, status });

test("waiting until every needed request is granted", () => {
  assert.equal(approvalState([{ id: "a" }, { id: "b" }], [rec("a", "granted"), rec("b", "pending")]).state, "waiting");
});

test("ready when all are granted, with the ids to retry", () => {
  const s = approvalState([{ id: "a" }, { id: "b" }], [rec("a", "granted"), rec("b", "granted")]);
  assert.deepEqual(s, { state: "ready", ids: ["a", "b"] });
});

test("denied wins over waiting", () => {
  assert.equal(approvalState([{ id: "a" }, { id: "b" }], [rec("a", "denied"), rec("b", "pending")]).state, "denied");
});

test("a request nobody has recorded yet is waiting", () => {
  assert.equal(approvalState([{ id: "a" }], []).state, "waiting");
});

test("a request the server marks own is the caller's, not theirs to decide", () => {
  const records = [{ id: "1", own: true }, { id: "2", own: false }, { id: "3" }];
  assert.equal(ownRequest(records[0]), true);
  assert.equal(ownRequest(records[1]), false);
  assert.equal(ownRequest(records[2]), false);
  assert.deepEqual(toDecide(records).map((r) => r.id), ["2", "3"]);
});
