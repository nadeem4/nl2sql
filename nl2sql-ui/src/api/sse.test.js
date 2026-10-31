import { test } from "node:test";
import assert from "node:assert/strict";
import { createSseParser } from "./sse.js";

function collect() {
  const frames = [];
  return { frames, parser: createSseParser((frame) => frames.push(frame)) };
}

test("a frame is its event name and its data", () => {
  const { frames, parser } = collect();
  parser.push('event: job.progress\ndata: {"step":"chinook","done":1}\n\n');
  assert.deepEqual(frames, [{ event: "job.progress", data: '{"step":"chinook","done":1}' }]);
});

test("a frame split anywhere is still one frame", () => {
  const text = 'event: job.finished\ndata: {"status":"succeeded"}\n\nevent: x\ndata: 2\n\n';
  for (let cut = 1; cut < text.length; cut += 1) {
    const { frames, parser } = collect();
    parser.push(text.slice(0, cut));
    parser.push(text.slice(cut));
    assert.deepEqual(frames, [
      { event: "job.finished", data: '{"status":"succeeded"}' },
      { event: "x", data: "2" },
    ], `cut at ${cut}`);
  }
});

test("a keep-alive comment is not an event", () => {
  const { frames, parser } = collect();
  parser.push(": keep-alive\n\n");
  parser.push(": keep-alive\n\nevent: a\ndata: 1\n\n: keep-alive\n\n");
  assert.deepEqual(frames, [{ event: "a", data: "1" }]);
});

test("CRLF line ends, several data lines and a frame without a name", () => {
  const { frames, parser } = collect();
  parser.push("event: a\r\ndata: one\r\ndata: two\r\n\r\n");
  parser.push("data: bare\n\n");
  assert.deepEqual(frames, [
    { event: "a", data: "one\ntwo" },
    { event: "message", data: "bare" },
  ]);
});

test("an event with no data line is dropped, and an unfinished frame never fires", () => {
  const { frames, parser } = collect();
  parser.push("event: lonely\n\n");
  parser.push("event: cut\ndata: half");
  parser.end();
  assert.deepEqual(frames, []);
});
