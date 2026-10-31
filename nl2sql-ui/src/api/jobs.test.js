import { test } from "node:test";
import assert from "node:assert/strict";
import { followJob } from "./jobs.js";

function fakeFetch(responses) {
  const encoder = new TextEncoder();
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url) => {
      const r = responses(String(url));
      if (r.stream) {
        return new Response(new ReadableStream({
          start(c) { c.enqueue(encoder.encode(r.stream)); c.close(); },
        }), { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return new Response(JSON.stringify(r.json), { status: 200, headers: { "content-type": "application/json" } });
  };
}

test("followJob resolves with the finished job from the stream", async () => {
  fakeFetch(() => ({ stream: 'event: job.finished\ndata: {"id":"j","status":"succeeded"}\n\n' }));
  assert.deepEqual(await followJob("j", () => {}), { id: "j", status: "succeeded" });
});

test("followJob falls back to the job record when the stream ends early", async () => {
  fakeFetch((url) =>
    url.endsWith("/events") ? { stream: 'event: job.progress\ndata: {"step":"a"}\n\n' } : { json: { id: "j", status: "running" } });
  const steps = [];
  assert.deepEqual(await followJob("j", (s) => steps.push(s)), { id: "j", status: "running" });
  assert.deepEqual(steps, [{ step: "a" }]);
});
