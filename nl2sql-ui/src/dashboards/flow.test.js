import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as api from "../api/dashboards.js";
import { openDashboardFlow, runAgain } from "./flow.js";
import { fakeApi } from "./fakeApi.js";

const view = JSON.parse(readFileSync(new URL("../api/fixtures/DashboardView.json", import.meta.url), "utf8"));
const run = (tileId, extra = {}) => ({ at: "2026-01-02T03:04:05Z", tiles: [{ tile_id: tileId, status: "ok", at: "2026-01-02T03:04:05Z", ...extra }] });

function recorder() {
  const seen = [];
  return {
    seen,
    handlers: {
      onView: (v) => seen.push(["view", v.dashboard.id]),
      onDue: (id, on) => seen.push(["due", id, on]),
      onRun: (r, id) => seen.push(["run", id]),
      onTileError: (id, e) => seen.push(["error", id, e.code]),
    },
  };
}

test("opening reads first, then refreshes each due tile on its own", async () => {
  const calls = fakeApi((method, path, body) => (method === "GET" ? { ...view, due: ["t1", "t2"] } : run(body.tile_id)));
  const { seen, handlers } = recorder();
  await openDashboardFlow(api, "d1", { ...handlers, live: () => true });
  assert.deepEqual(calls.map((c) => [c.method, c.path, c.body]), [
    ["GET", "/dashboards/d1", undefined],
    ["POST", "/dashboards/d1/refresh", { tile_id: "t1" }],
    ["POST", "/dashboards/d1/refresh", { tile_id: "t2" }],
  ]);
  assert.deepEqual(seen.map((s) => s[0]), ["view", "due", "run", "due", "due", "run", "due"]);
});

test("nothing is applied once the page has moved on", async () => {
  let open = true;
  const calls = fakeApi((method) => { if (method === "GET") return { ...view, due: ["t1", "t2"] }; open = false; return run("t1"); });
  const { seen, handlers } = recorder();
  await openDashboardFlow(api, "d1", { ...handlers, live: () => open });
  assert.equal(calls.length, 2, "stops after the first refresh, no second one");
  assert.equal(seen.filter((s) => s[0] === "run").length, 0);
});

test("one failing tile does not stop the others", async () => {
  fakeApi((method, path, body) => {
    if (method === "GET") return { ...view, due: ["t1", "t2"] };
    return body.tile_id === "t1" ? { __status: 409, code: "DashboardBusy", message: "busy" } : run("t2");
  });
  const { seen, handlers } = recorder();
  await openDashboardFlow(api, "d1", { ...handlers, live: () => true });
  assert.deepEqual(seen.filter((s) => s[0] === "error" || s[0] === "run"), [["error", "t1", "DashboardBusy"], ["run", "t2"]]);
});

test("a dashboard that cannot be opened rejects", async () => {
  fakeApi(() => ({ __status: 404, code: "DashboardNotFound", message: "gone" }));
  await assert.rejects(openDashboardFlow(api, "d1", { ...recorder().handlers, live: () => true }), { code: "DashboardNotFound" });
});

const needs = (ids) => run("t4", { status: "needs_approval", approvals_needed: ids.map((id) => ({ id, reason: "r" })) });

test("run again with the ids carries them in the refresh", async () => {
  const calls = fakeApi(() => run("t4"));
  const out = await runAgain(api, "d1", { tileId: "t4", approvalIds: ["a1"], filters: [], selection: {} });
  assert.deepEqual(calls.map((c) => c.body), [{ tile_id: "t4", approval_ids: ["a1"] }]);
  assert.equal(out.waiting, false);
});

test("run again with no ids refreshes once to learn them, then sends them", async () => {
  const calls = fakeApi((method, path, body) => (body.approval_ids ? run("t4") : needs(["a9"])));
  const out = await runAgain(api, "d1", { tileId: "t4", approvalIds: [], filters: [], selection: {} });
  assert.deepEqual(calls.map((c) => c.body), [{ tile_id: "t4" }, { tile_id: "t4", approval_ids: ["a9"] }]);
  assert.equal(out.run.tiles[0].status, "ok");
});

test("a tile that needs no approval after all stops at the first refresh", async () => {
  const calls = fakeApi(() => run("t4"));
  await runAgain(api, "d1", { tileId: "t4", approvalIds: [], filters: [], selection: {} });
  assert.equal(calls.length, 1);
});

test("an approval still waiting is reported, and the run that named it is kept", async () => {
  fakeApi((method, path, body) => (body.approval_ids ? { __status: 404, code: "ApprovalNotFound", message: "x" } : needs(["a9"])));
  const out = await runAgain(api, "d1", { tileId: "t4", approvalIds: [], filters: [], selection: {} });
  assert.equal(out.waiting, true);
  assert.equal(out.run.tiles[0].approvals_needed[0].id, "a9");
});

test("run again sends only the filters that were applied", async () => {
  const calls = fakeApi(() => run("t1"));
  const filters = [{ id: "f1", op: "in", kind: "text", label: "C" }];
  await runAgain(api, "d1", { tileId: "t1", approvalIds: [], filters, selection: { f1: ["US"] } });
  assert.deepEqual(calls[0].body, { tile_id: "t1", filters: { f1: ["US"] } });
});
