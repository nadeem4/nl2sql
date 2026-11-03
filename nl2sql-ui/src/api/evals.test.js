// The calls the Evals page makes, pinned to what routes/evals.py and routes/jobs.py read.
import { test } from "node:test";
import assert from "node:assert/strict";
import { listScorers, listSuites, startEval } from "./evals.js";

let calls;
function fakeFetch() {
  calls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
    return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
  };
}

test("suites and scorers are reads", async () => {
  fakeFetch();
  await listSuites();
  await listScorers();
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [["GET", "/api/v1/evals/suites"], ["GET", "/api/v1/evals/scorers"]]);
});

test("a run sends only what was chosen", async () => {
  fakeFetch();
  await startEval({ mode: "plans" });
  await startEval({ mode: "full", suites: ["chinook"], cases: ["q1", "q2"], maxCostUsd: 2.5 });
  assert.deepEqual(calls.map((c) => c.body), [
    { mode: "plans" },
    { mode: "full", suites: ["chinook"], cases: ["q1", "q2"], max_cost_usd: 2.5 },
  ]);
  assert.equal(calls[0].url, "/api/v1/jobs/eval");
});

test("an empty list of suites or cases is left out, so the server runs them all", async () => {
  fakeFetch();
  await startEval({ mode: "retrieval", suites: [], cases: [] });
  assert.deepEqual(calls[0].body, { mode: "retrieval" });
});
