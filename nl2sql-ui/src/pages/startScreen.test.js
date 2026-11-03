import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as jobs from "../api/jobs.js";
import * as metaApi from "../api/meta.js";
import { fakeApi } from "../dashboards/fakeApi.js";
import { HOSTED_STEPS, SELF_HOST_GUIDE, demoFlow, startView } from "./startScreen.js";

const META = JSON.parse(readFileSync(new URL("../api/fixtures/Meta.json", import.meta.url), "utf8"));
const ADMIN = { id: "root", admin: true };
const VISITOR = { id: null, admin: false };

test("the three start values from /meta decide the screen", () => {
  assert.equal(startView({ ...META, start: null }, ADMIN), null);
  assert.deepEqual(startView({ ...META, start: "choose" }, ADMIN), { kind: "choose", canAct: true });
  assert.deepEqual(startView({ ...META, start: "hosted" }, VISITOR), { kind: "hosted" });
});

test("the fixture's start is read as the server wrote it", () => {
  assert.equal(META.start, null);
  assert.equal(startView(META, ADMIN), null);
  assert.equal(startView(null, ADMIN), null);
  assert.equal(startView({}, ADMIN), null);
});

test("on an empty project only an admin is offered the two actions", () => {
  assert.equal(startView({ start: "choose" }, VISITOR).canAct, false);
  assert.equal(startView({ start: "choose" }, ADMIN).canAct, true);
  // A caller /meta did not name as an admin is offered neither.
  assert.equal(startView({ start: "choose" }, null).canAct, false);
});

test("hosted Connect shows run-it-yourself steps and calls no route", () => {
  const calls = fakeApi({});
  const view = startView({ start: "hosted" }, VISITOR);
  assert.equal(view.kind, "hosted");
  const text = HOSTED_STEPS.map((step) => `${step.label} ${(step.code || []).join(" ")}`).join("\n");
  assert.match(text, /pip install/);
  assert.match(text, /docker compose/i);
  assert.match(SELF_HOST_GUIDE, /deployment\/compose/);
  assert.deepEqual(calls, []);
});

test("Try the demo posts /jobs/demo, follows the job, and finishes when it succeeds", async () => {
  const queued = { id: "job1", kind: "demo", status: "queued", progress: [], result: null, error: null };
  const calls = fakeApi((method, path) => (path === "/jobs/demo" ? queued : { ...queued, status: "succeeded" }));
  const seen = [];
  const out = await demoFlow({ startDemo: metaApi.startDemo, follow: jobs.followJob }, { onJob: (j) => seen.push(j.status) });
  assert.deepEqual(out, { done: true });
  assert.deepEqual(calls[0], { method: "POST", path: "/jobs/demo", body: {} });
  assert.equal(seen[0], "queued");
  assert.equal(seen.at(-1), "succeeded");
});

test("a failed demo job throws its message and fix", async () => {
  const failed = { id: "job1", kind: "demo", status: "failed", progress: [], result: null,
    error: { message: "Could not index.", fix: "Check the sample databases." } };
  fakeApi((method, path) => (path === "/jobs/demo" ? { ...failed, status: "queued", error: null } : failed));
  await assert.rejects(
    demoFlow({ startDemo: metaApi.startDemo, follow: jobs.followJob }, {}),
    (err) => err.message === "Could not index." && err.fix === "Check the sample databases.",
  );
});

test("a cancelled demo job is not an error", async () => {
  const job = { id: "job1", kind: "demo", status: "cancelled", progress: [], result: null, error: null };
  fakeApi(job);
  assert.deepEqual(await demoFlow({ startDemo: metaApi.startDemo, follow: jobs.followJob }, {}), { cancelled: true });
});

test("cancelling a running demo is the job's own cancel, and the flow ends as cancelled, not as a fault", async () => {
  const queued = { id: "job1", kind: "demo", status: "queued", progress: [], result: null, error: null };
  const calls = fakeApi((method, path) => (path === "/jobs/demo" ? queued : { ...queued, status: "cancelled" }));
  const flow = await demoFlow({ startDemo: metaApi.startDemo, follow: jobs.followJob }, {});
  assert.deepEqual(flow, { cancelled: true });
  await jobs.cancelJob("job1");
  assert.deepEqual(calls.at(-1), { method: "DELETE", path: "/jobs/job1", body: undefined });
});
