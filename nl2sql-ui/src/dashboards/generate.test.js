import { test } from "node:test";
import assert from "node:assert/strict";
import * as dashboards from "../api/dashboards.js";
import * as jobs from "../api/jobs.js";
import { fakeApi } from "./fakeApi.js";
import { describeGenerateError, findRunning, generateBody, generateFlow, resumeFlow, withStep } from "./generate.js";

const queued = {
  id: "job1", kind: "dashboard", status: "queued", params: { datasource_id: "chinook" }, created_by: "ann",
  created_at: "2026-01-01T00:00:00Z", started_at: null, finished_at: null, progress: [], result: null, error: null,
};
const stepAt = (step, done, total) => ({ at: "2026-01-01T00:00:01Z", step, done, total });
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
  const step = stepAt("tile 2 of 8", 2, 8);
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
    follow: async (id, onStep) => { onStep(stepAt("tile 1 of 2", 1, 2)); return finished(); },
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

function streamFetch(frames, record) {
  const encoder = new TextEncoder();
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init = {}) => {
    const path = String(url).replace("/api/v1", "");
    record.push([init.method, path]);
    if (path === "/jobs/dashboard") return new Response(JSON.stringify(queued), { status: 202 });
    return new Response(new ReadableStream({ start(c) { c.enqueue(encoder.encode(frames)); c.close(); } }),
      { status: 200, headers: { "content-type": "text/event-stream" } });
  };
}

test("a success through the live progress stream", async () => {
  const done = finished();
  const frames = `event: job.progress
data: ${JSON.stringify(stepAt("tile 1 of 2", 1, 2))}

` +
    `event: job.progress
data: ${JSON.stringify(stepAt("tile 2 of 2", 2, 2))}

` +
    `event: job.finished
data: ${JSON.stringify(done)}

`;
  const record = [];
  streamFetch(frames, record);
  const counts = [];
  const out = await generateFlow({ startJob: dashboards.startDashboardJob, follow: jobs.followJob }, { datasourceId: "chinook" },
    { onJob: (j) => counts.push(j.progress.length) });
  assert.deepEqual(out, { hash: "#/dashboards/d9", dashboard: made });
  assert.deepEqual(counts, [0, 1, 2, 0]);
  assert.deepEqual(record[1], ["GET", "/jobs/job1/events"]);
});

test("the abort signal reaches the follower, and an aborted flow opens nothing", async () => {
  const controller = new AbortController();
  let given = null;
  const api = {
    startJob: async () => queued,
    follow: async (id, onStep, signal) => { given = signal; controller.abort(); return finished(); },
  };
  const out = await generateFlow(api, { datasourceId: "a" }, { signal: controller.signal });
  assert.equal(given, controller.signal);
  assert.deepEqual(out, { aborted: true });
});

test("a flow aborted before the job starts never follows it", async () => {
  const controller = new AbortController();
  let followed = false;
  const api = { startJob: async () => { controller.abort(); return queued; }, follow: async () => { followed = true; return finished(); } };
  assert.deepEqual(await generateFlow(api, { datasourceId: "a" }, { signal: controller.signal }), { aborted: true });
  assert.equal(followed, false);
});

test("a queued or running dashboard job is found among the caller's jobs", () => {
  const list = [
    { ...queued, id: "i", kind: "index", status: "running" },
    { ...queued, id: "old", status: "succeeded" },
    { ...queued, id: "now", status: "running" },
  ];
  assert.equal(findRunning(list).id, "now");
  assert.equal(findRunning([list[0], list[1]]), null);
  assert.equal(findRunning(null), null);
});

test("resuming follows the job already running and opens its dashboard", async () => {
  const calls = [];
  const api = { follow: async (id, onStep) => { calls.push(id); onStep(stepAt("tile 1 of 2", 1, 2)); return finished(); } };
  const seen = [];
  const out = await resumeFlow(api, { ...queued, status: "running" }, { onJob: (j) => seen.push(j.progress.length) });
  assert.deepEqual(calls, ["job1"]);
  assert.deepEqual(seen, [0, 1, 0]);
  assert.deepEqual(out, { hash: "#/dashboards/d9", dashboard: made });
});

test("onStarted fires when the start request has returned, not before", async () => {
  const order = [];
  const api = {
    startJob: async () => { order.push("start"); return queued; },
    follow: async () => { order.push("follow"); return finished(); },
  };
  await generateFlow(api, { datasourceId: "a" }, { onStarted: () => order.push("started") });
  assert.deepEqual(order, ["start", "started", "follow"]);
});

test("onStarted also fires when the start request fails, so the form unlocks", async () => {
  let started = 0;
  const api = { startJob: async () => { throw Object.assign(new Error("busy"), { status: 409 }); }, follow: async () => finished() };
  await assert.rejects(generateFlow(api, { datasourceId: "a" }, { onStarted: () => { started += 1; } }));
  assert.equal(started, 1);
});
