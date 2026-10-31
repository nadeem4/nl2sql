import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createHttp, readApiKey, writeApiKey } from "./http.js";

function memoryStorage(seed = {}) {
  const items = { ...seed };
  return {
    getItem: (k) => (k in items ? items[k] : null),
    setItem: (k, v) => { items[k] = String(v); },
    removeItem: (k) => { delete items[k]; },
  };
}

// A response as fetch gives one: status, headers, a JSON body and a byte stream.
function reply(status, body, { headers = {}, chunks } = {}) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  const bytes = new TextEncoder();
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    headers: { get: (name) => lower[name.toLowerCase()] ?? null },
    text: async () => text,
    json: async () => JSON.parse(text),
    body: {
      getReader() {
        const parts = (chunks || [text]).map((c) => bytes.encode(c));
        return { read: async () => (parts.length ? { done: false, value: parts.shift() } : { done: true }) };
      },
    },
  };
}

function client({ storage = memoryStorage(), answer = () => reply(200, {}) } = {}) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url, ...init });
    return answer(url, init);
  };
  return { calls, storage, http: createHttp({ fetch, storage }) };
}

const ENVELOPE = {
  error: { code: "NOT_FOUND", message: "No such job.", fix: "List jobs.", details: [], request_id: null },
};

test("a request goes under /api/v1 and returns the JSON it gets", async () => {
  const { calls, http } = client({ answer: () => reply(200, { id: "j1" }) });
  assert.deepEqual(await http.request("GET", "/jobs/j1"), { id: "j1" });
  assert.equal(calls[0].url, "/api/v1/jobs/j1");
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].body, undefined);
  assert.equal(calls[0].headers["Content-Type"], undefined);
});

test("a body is sent as JSON, and a 204 answers null", async () => {
  const { calls, http } = client({ answer: () => reply(204) });
  assert.equal(await http.request("POST", "/jobs/index", { datasource_ids: ["a"] }), null);
  assert.equal(calls[0].body, '{"datasource_ids":["a"]}');
  assert.equal(calls[0].headers["Content-Type"], "application/json");
});

test("the tab's API key is the bearer identity, and only when there is one", async () => {
  const none = client();
  await none.http.request("GET", "/ready");
  assert.equal(none.calls[0].headers.Authorization, undefined);

  const held = client();
  writeApiKey(held.storage, "  nl2sql_abcdefghijklmnopqrstuvwxyz  ");
  assert.equal(readApiKey(held.storage), "nl2sql_abcdefghijklmnopqrstuvwxyz");
  await held.http.request("GET", "/ready");
  assert.equal(held.calls[0].headers.Authorization, "Bearer nl2sql_abcdefghijklmnopqrstuvwxyz");

  writeApiKey(held.storage, "");
  assert.equal(readApiKey(held.storage), "");
});

test("the tab's model keys and step models ride as the override headers", async () => {
  const storage = memoryStorage({
    "nl2sql.playground.apiKey.anthropic": "sk-ant-aaaaaaaaaaaaaaaaaaaa",
    "nl2sql.playground.stepModels": JSON.stringify({ ast_planner: "anthropic:claude-opus-5" }),
  });
  const { calls, http } = client({ storage });
  await http.request("POST", "/query", { question: "q" });
  const sent = calls[0].headers;
  assert.equal(sent["X-NL2SQL-Api-Key-anthropic"], "sk-ant-aaaaaaaaaaaaaaaaaaaa");
  assert.equal(sent["X-NL2SQL-Models"], '{"ast_planner":"anthropic:claude-opus-5"}');
});

test("a storage that throws is no key and no failure", async () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const { calls, http } = client({ storage: broken });
  await http.request("GET", "/ready");
  assert.equal(calls[0].headers.Authorization, undefined);
  assert.doesNotThrow(() => writeApiKey(broken, "x"));
});

test("the error envelope becomes an ApiError", async () => {
  const { http } = client({ answer: () => reply(404, ENVELOPE, { headers: { "X-Request-ID": "r-1" } }) });
  await assert.rejects(http.request("GET", "/jobs/nope"), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 404);
    assert.equal(e.code, "NOT_FOUND");
    assert.equal(e.message, "No such job.");
    assert.equal(e.fix, "List jobs.");
    assert.deepEqual(e.details, []);
    assert.equal(e.requestId, "r-1");
    return true;
  });
});

test("a 500's request id comes from the envelope first", async () => {
  const body = { error: { code: "INTERNAL", message: "Something failed.", fix: null, details: [], request_id: "abc" } };
  const { http } = client({ answer: () => reply(500, body, { headers: { "X-Request-ID": "other" } }) });
  await assert.rejects(http.request("GET", "/x"), (e) => e.requestId === "abc" && e.code === "INTERNAL");
});

test("a 422 keeps its per-problem details", async () => {
  const details = [{ loc: ["body", "question"], msg: "Field required" }];
  const body = { error: { code: "INVALID_REQUEST", message: "The request does not validate.", fix: "Fix it.", details } };
  const { http } = client({ answer: () => reply(422, body) });
  await assert.rejects(http.request("POST", "/query", {}), (e) => e.details[0].loc[1] === "question");
});

test("an answer that is not the envelope still says what failed", async () => {
  const { http } = client({ answer: () => reply(502, "<html>Bad gateway</html>") });
  await assert.rejects(http.request("GET", "/x"), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 502);
    assert.equal(e.code, "HTTP_502");
    assert.match(e.message, /502/);
    return true;
  });
});

test("a server that cannot be reached is an ApiError with status 0", async () => {
  const http = createHttp({ fetch: async () => { throw new TypeError("fetch failed"); }, storage: memoryStorage() });
  await assert.rejects(http.request("GET", "/x"), (e) => e instanceof ApiError && e.status === 0 && e.code === "NETWORK");
});

test("stream hands over each event, parsed, and skips the keep-alives", async () => {
  const chunks = [
    'event: job.progress\ndata: {"step":"a","done":1}\n\n: keep-al',
    'ive\n\nevent: job.finished\ndata: {"status":"succeeded"}',
    "\n\n",
  ];
  const { calls, http } = client({ answer: () => reply(200, "", { chunks, headers: { "Content-Type": "text/event-stream" } }) });
  const seen = [];
  await http.stream("/jobs/j1/events", undefined, (e) => seen.push(e));
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].headers.Accept, "text/event-stream");
  assert.deepEqual(seen, [
    { event: "job.progress", data: { step: "a", done: 1 } },
    { event: "job.finished", data: { status: "succeeded" } },
  ]);
});

test("stream with a body is a POST and carries the same headers", async () => {
  const storage = memoryStorage();
  writeApiKey(storage, "nl2sql_abcdefghijklmnopqrstuvwxyz");
  const { calls, http } = client({ storage, answer: () => reply(200, "event: a\ndata: not json\n\n") });
  const seen = [];
  await http.stream("/query/stream", { question: "q" }, (e) => seen.push(e));
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].body, '{"question":"q"}');
  assert.equal(calls[0].headers.Authorization, "Bearer nl2sql_abcdefghijklmnopqrstuvwxyz");
  // Data that is not JSON arrives as the text it was.
  assert.deepEqual(seen, [{ event: "a", data: "not json" }]);
});

test("stream that is refused throws the ApiError before any event", async () => {
  const { http } = client({ answer: () => reply(404, ENVELOPE) });
  const seen = [];
  await assert.rejects(http.stream("/jobs/x/events", undefined, (e) => seen.push(e)), (e) => e.code === "NOT_FOUND");
  assert.deepEqual(seen, []);
});

test("stream passes the caller's abort signal to fetch", async () => {
  const { calls, http } = client({ answer: () => reply(200, "") });
  const controller = new AbortController();
  await http.stream("/jobs/j/events", undefined, () => {}, controller.signal);
  assert.equal(calls[0].signal, controller.signal);
});
