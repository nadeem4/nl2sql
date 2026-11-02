import { test } from "node:test";
import assert from "node:assert/strict";
import * as dashboards from "../api/dashboards.js";
import * as jobs from "../api/jobs.js";
import { fakeApi } from "./fakeApi.js";
import { describeGenerateError, generateBody, generateFlow, withStep } from "./generate.js";

const queued = { id: "job1", kind: "dashboard", status: "queued", progress: [], result: null, error: null };
const made = { id: "d9", workspace_id: "w2", title: "Starter" };
const finished = (extra = {}) => ({ ...queued, status: "succeeded", result: { dashboard: made, run: { tiles: [] } }, ...extra });

test("only the fields that were set are sent", () => {
  assert.deepEqual(generateBody({ datasourceId: "chinook" }), { datasource_id: "chinook" });
  assert.deepEqual(generateBody({ datasourceId: "chinook", title: "  ", workspaceId: "", maxTiles: "" }), { datasource_id: "chinook" });
  assert.deepEqual(
    generateBody({ datasourceId: "chinook", title: " Sales ", workspaceId: "w1", maxTiles: "6" }),
    { datasource_id: "chinook", title: "Sales", workspace_id: "w1", max_tiles: 6 },
  );
});

test("a chart count under one is not sent", () => {
  assert.deepEqual(generateBody({ datasourceId: "a", maxTiles: "0" }), { datasource_id: "a" });
  assert.deepEqual(generateBody({ datasourceId: "a", maxTiles: "x" }), { datasource_id: "a" });
});

test("each progress step is added to the job, which is then running", () => {
  const step = { step: "tile 2 of 8", done: 2, total: 8 };
  assert.deepEqual(withStep(queued, step), { ...queued, status: "running", progress: [step] });
  assert.equal(withStep(null, step), null);
});

test("a finished job opens the new dashboard in its workspace", async () => {
  const calls = fakeApi((method, path) => (path === "/jobs/dashboard" ? queued : finished()));
  const seen = [];
  const out = await generateFlow({ startJob: dashboards.startDashboardJob, follow: jobs.followJob }, { datasourceId: "chinook", title: "Starter" }, {
    onJob: (j) => seen.push(j.status),
  });
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/jobs/dashboard");
  assert.deepEqual(calls[0].body, { datasource_id: "chinook", title: "Starter" });
  assert.deepEqual(out, { hash: "#/dashboards/d9", dashboard: made });
  assert.equal(seen[0], "queued");
  assert.equal(seen[seen.length - 1], "succeeded");
});

test("progress from the stream reaches the page as it comes", async () => {
  const steps = [];
  const api = {
    startJob: async () => queued,
    follow: async (id, onStep) => { onStep({ step: "tile 1 of 2", done: 1, total: 2 }); return finished(); },
  };
  await generateFlow(api, { datasourceId: "a" }, { onJob: (j) => steps.push(j.progress.length) });
  assert.deepEqual(steps, [0, 1, 0]);
});

test("a failed job throws its own sentence", async () => {
  const api = { startJob: async () => queued, follow: async () => ({ ...queued, status: "failed", error: { message: "No tables to chart.", fix: "Index the datasource." } }) };
  await assert.rejects(generateFlow(api, { datasourceId: "a" }, {}), (e) => e.message === "No tables to chart." && e.fix === "Index the datasource.");
});

test("a cancelled job opens nothing", async () => {
  const api = { startJob: async () => queued, follow: async () => ({ ...queued, status: "cancelled" }) };
  assert.deepEqual(await generateFlow(api, { datasourceId: "a" }, {}), { cancelled: true });
});

test("a job that is still running when the stream ends is reported, not opened", async () => {
  const api = { startJob: async () => queued, follow: async () => ({ ...queued, status: "running" }) };
  await assert.rejects(generateFlow(api, { datasourceId: "a" }, {}), /still running/);
});

test("a busy caller (409) and a bad request (422) are told in a sentence", () => {
  assert.match(describeGenerateError({ status: 409, message: "x" }), /already have a job/);
  assert.equal(describeGenerateError({ status: 422, message: "max_tiles must be at least 1.", fix: "Use 1 or more." }), "max_tiles must be at least 1. Use 1 or more.");
  assert.equal(describeGenerateError({ status: 404, message: "No such datasource." }), "No such datasource.");
  assert.equal(describeGenerateError(new Error("boom")), "boom");
});
