// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EMPTY_PROGRESS, StreamUnavailable, liveResult, liveStations, parseSse, progressOf, streamAsk,
} from "./stream.js";

// ---------- parsing ----------

test("parseSse reads whole events and keeps a half-sent one for later", () => {
  const { events, rest } = parseSse(
    'event: node_started\ndata: {"node":"decomposer"}\n\nevent: answer_delta\ndata: {"field":"summ',
  );
  assert.deepEqual(events, [{ event: "node_started", data: { node: "decomposer" } }]);
  const next = parseSse(`${rest}ary","text":"Rock"}\n\n`);
  assert.deepEqual(next.events, [{ event: "answer_delta", data: { field: "summary", text: "Rock" } }]);
  assert.equal(next.rest, "");
});

test("parseSse takes CRLF line ends, joins data lines, and skips comments and broken JSON", () => {
  const { events } = parseSse(': keep-alive\r\n\r\nevent: final\r\ndata: {"a":\r\ndata: 1}\r\n\r\nevent: x\ndata: {nope\n\n');
  assert.deepEqual(events, [{ event: "final", data: { a: 1 } }]);
});

test("an event with no name is a message", () => {
  assert.deepEqual(parseSse('data: {"n":1}\n\n').events, [{ event: "message", data: { n: 1 } }]);
});

// ---------- progress ----------

const run = (...events) => events.reduce(progressOf, EMPTY_PROGRESS);
const started = (node) => ({ event: "node_started", data: { node } });
const finished = (node, seconds = 0.5) => ({ event: "node_finished", data: { node, seconds, ok: true } });

test("progress follows the steps as they start and finish", () => {
  const p = run(started("decomposer"), finished("decomposer", 1.2), started("ast_planner"));
  assert.equal(p.live, "ast_planner");
  assert.deepEqual(p.finished, ["decomposer"]);
  assert.deepEqual(p.timings, { decomposer: 1.2 });
});

test("a step run twice (a retry) keeps its longest time", () => {
  const p = run(started("ast_planner"), finished("ast_planner", 2), started("ast_planner"), finished("ast_planner", 1));
  assert.equal(p.timings.ast_planner, 2);
  assert.deepEqual(p.finished, ["ast_planner"]);
});

test("the answer grows field by field as it is written", () => {
  const delta = (field, text) => ({ event: "answer_delta", data: { field, text } });
  const p = run(delta("summary", "Rock "), delta("summary", "leads."), delta("content", "Rock has 1,297."));
  assert.deepEqual(p.answer, { summary: "Rock leads.", content: "Rock has 1,297." });
});

test("the manifest gives the rows station what it pages from", () => {
  const results = [{ node_id: "sq_1", columns: ["genre"], row_count: 25, truncated: false }];
  const p = run({ event: "result_manifest", data: { trace_id: "t-1", results } });
  assert.deepEqual(liveResult(p), { trace_id: "t-1", result_manifest: results, status: "running" });
  assert.equal(liveResult(EMPTY_PROGRESS), null);
});

test("progressOf leaves the state alone for an event it does not know", () => {
  assert.equal(progressOf(EMPTY_PROGRESS, { event: "ping", data: {} }), EMPTY_PROGRESS);
});

// ---------- stations while the run is live ----------

test("before any step reports, the plan station is live and the rest wait", () => {
  assert.deepEqual(liveStations(EMPTY_PROGRESS), {
    plan: "busy", checks: "queued", sql: "queued", rows: "queued", cost: "queued",
  });
});

test("each station lights as its step finishes, and the next one goes live", () => {
  const p = run(started("ast_planner"), finished("ast_planner"), started("logical_validator"));
  assert.deepEqual(liveStations(p), {
    plan: "done", checks: "busy", sql: "queued", rows: "queued", cost: "queued",
  });
  const q = run(...["ast_planner", "logical_validator", "generator", "executor"].flatMap((n) => [started(n), finished(n)]));
  assert.deepEqual(liveStations(q), { plan: "done", checks: "done", sql: "done", rows: "done", cost: "busy" });
});

// ---------- the request ----------

function sseResponse(chunks, { status = 200 } = {}) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

test("streamAsk hands over every event and resolves with the final result", async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    assert.equal(url, "/api/ask/stream");
    assert.equal(init.method, "POST");
    return sseResponse(['event: node_started\ndata: {"node":"decomposer"}\n\nevent: fi',
      'nal\ndata: {"status":"success"}\n\n']);
  };
  const final = await streamAsk(fetchImpl, "/api/ask/stream", { method: "POST" }, (e) => seen.push(e.event));
  assert.deepEqual(final, { status: "success" });
  assert.deepEqual(seen, ["node_started", "final"]);
});

test("an error event is the run's error, in its own words", async () => {
  const fetchImpl = async () => sseResponse(['event: error\ndata: {"message":"The run failed: no graph"}\n\n']);
  await assert.rejects(streamAsk(fetchImpl, "/u", {}, () => {}), (e) =>
    !(e instanceof StreamUnavailable) && e.message === "The run failed: no graph");
});

test("a refusal before the stream is the server's sentence, not a fallback", async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ detail: "Slow down." }), { status: 429 });
  await assert.rejects(streamAsk(fetchImpl, "/u", {}, () => {}), (e) =>
    !(e instanceof StreamUnavailable) && e.message === "Slow down.");
});

test("a server without the stream route, or a stream cut short, falls back", async () => {
  const missing = async () => new Response("{}", { status: 404 });
  await assert.rejects(streamAsk(missing, "/u", {}, () => {}), StreamUnavailable);
  const cut = async () => sseResponse(['event: node_started\ndata: {"node":"decomposer"}\n\n']);
  await assert.rejects(streamAsk(cut, "/u", {}, () => {}), StreamUnavailable);
  const broken = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(streamAsk(broken, "/u", {}, () => {}), StreamUnavailable);
});

test("a browser that cannot read a body as it arrives reads it whole", async () => {
  const text = 'event: node_started\ndata: {"node":"decomposer"}\n\nevent: final\ndata: {"status":"success"}\n\n';
  const fetchImpl = async () => ({ ok: true, status: 200, body: null, text: async () => text });
  const seen = [];
  assert.deepEqual(await streamAsk(fetchImpl, "/u", {}, (e) => seen.push(e.event)), { status: "success" });
  assert.deepEqual(seen, ["node_started", "final"]);
});

test("Stop is an abort, never a fallback that asks again", async () => {
  const fetchImpl = async () => { throw new DOMException("The user aborted a request.", "AbortError"); };
  await assert.rejects(streamAsk(fetchImpl, "/u", {}, () => {}), (e) => e.name === "AbortError");
});

test("no fetch streaming in this browser at all falls back", async () => {
  await assert.rejects(streamAsk(undefined, "/u", {}, () => {}), StreamUnavailable);
});
