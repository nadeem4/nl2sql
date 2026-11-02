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

test("a request the caller asked is theirs, not theirs to decide", () => {
  const records = [{ id: "1", request: { asker_user_id: "me" } }, { id: "2", request: { asker_user_id: "you" } },
    { id: "3", request: { asker_user_id: null } }];
  assert.equal(ownRequest(records[0], "me"), true);
  assert.equal(ownRequest(records[1], "me"), false);
  assert.equal(ownRequest(records[2], null), false);
  assert.deepEqual(toDecide(records, "me").map((r) => r.id), ["2", "3"]);
  assert.equal(toDecide(records, null).length, 3);
});
