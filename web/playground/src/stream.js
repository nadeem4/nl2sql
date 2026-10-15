// The Ask page's streamed run (`POST /api/ask/stream`), as plain data: the
// Server-Sent Events parser, the progress the events add up to, the station
// states while the run is live, and the request itself. No React here, so
// `npm test` covers it with node's own test runner.
//
// The server sends the engine's run events (`engine.stream_query`): `run.started`,
// `node.started` / `node.finished` per step, `result.manifest` once the results
// are saved, `answer.delta` as the answer is written, and then one
// `run.finished` (its `data.result` is exactly what `/api/ask` returns) or
// `run.failed`. Each SSE data line is the whole event as JSON: `name`, `at`,
// `trace_id`, `tenant_id` and `data`.

// Splits what has arrived into whole events, keeping a half-sent one for the
// next read: `{ event, data, traceId }`, `data` being the event's own data. A
// comment or an unreadable event is skipped.
export function parseSse(buffer) {
  const text = buffer.replace(/\r\n/g, "\n");
  const blocks = text.split("\n\n");
  const rest = blocks.pop();
  const events = [];
  for (const block of blocks) {
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const at = line.indexOf(":");
      const field = at < 0 ? line : line.slice(0, at);
      const value = at < 0 ? "" : line.slice(at + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (!data.length) continue;
    try {
      const whole = JSON.parse(data.join("\n"));
      events.push({ event, data: (whole && whole.data) || {}, traceId: (whole && whole.trace_id) || null });
    } catch {
      /* not JSON: nothing this page can read */
    }
  }
  return { events, rest };
}

export const EMPTY_PROGRESS = Object.freeze({
  live: null,
  finished: [],
  timings: {},
  answer: { summary: "", content: "" },
  manifest: null,
  traceId: null,
});

// What the events so far add up to. A step that runs twice (a retry, a
// second sub-query) keeps its longest time, as the run's own timings do.
export function progressOf(progress, { event, data, traceId }) {
  const d = data || {};
  switch (event) {
    case "node.started":
      return { ...progress, live: d.node };
    case "node.finished": {
      const prior = progress.timings[d.node];
      const seconds = Number(d.seconds) || 0;
      return {
        ...progress,
        live: progress.live === d.node ? null : progress.live,
        finished: progress.finished.includes(d.node) ? progress.finished : [...progress.finished, d.node],
        timings: { ...progress.timings, [d.node]: prior === undefined ? seconds : Math.max(prior, seconds) },
      };
    }
    case "result.manifest":
      return { ...progress, manifest: d.results || [], traceId: traceId || null };
    case "answer.delta":
      if (!(d.field in progress.answer)) return progress;
      return { ...progress, answer: { ...progress.answer, [d.field]: progress.answer[d.field] + (d.text || "") } };
    default:
      return progress;
  }
}

// The stations, the node each one waits on, in the order the run reaches them.
const STEPS = [
  ["plan", "ast_planner"],
  ["checks", "logical_validator"],
  ["sql", "generator"],
  ["rows", "executor"],
];

// While the run is live: a station whose step finished is done, the first
// one still waiting is live, the rest are queued. Cost & time is the whole
// run, so it is live once the rows are in -- the answer is being written --
// and done only when the result is back.
export function liveStations(progress) {
  const states = {};
  let live = false;
  for (const [station, node] of STEPS) {
    if (progress.finished.includes(node)) states[station] = "done";
    else if (!live) {
      states[station] = "busy";
      live = true;
    } else states[station] = "queued";
  }
  states.cost = live ? "queued" : "busy";
  return states;
}

// Enough of a result for the Rows station to page the saved results before
// the run is over, or null until the manifest has arrived.
export function liveResult(progress) {
  if (!progress || !progress.manifest || !progress.traceId) return null;
  return { trace_id: progress.traceId, result_manifest: progress.manifest, status: "running" };
}

// Thrown when the stream could not be used -- the browser cannot stream a
// fetch, the server has no stream route, or the stream broke before its last
// event -- so the page asks `/api/ask` instead.
export class StreamUnavailable extends Error {
  constructor(reason) {
    super(reason);
    this.name = "StreamUnavailable";
  }
}

async function refusal(response) {
  const reply = await response.json().catch(() => ({}));
  return new Error(typeof reply.detail === "string" ? reply.detail : `The server answered ${response.status}.`);
}

// POSTs the question and hands each event to `onEvent` as it arrives.
// Resolves with the final result; rejects with the run's error, the server's
// refusal (a 4xx before the stream: no key, a limit), an AbortError on Stop,
// or StreamUnavailable when the page should fall back to `/api/ask`.
export async function streamAsk(fetchImpl, url, init, onEvent) {
  if (typeof fetchImpl !== "function" || typeof TextDecoder !== "function") {
    throw new StreamUnavailable("This browser cannot read a streamed answer.");
  }
  let response;
  try {
    response = await fetchImpl(url, { ...init, headers: { ...(init && init.headers), Accept: "text/event-stream" } });
  } catch (e) {
    if (e && e.name === "AbortError") throw e;
    throw new StreamUnavailable(e && e.message ? e.message : "The stream could not be opened.");
  }
  // An older server has no stream route; everything else not OK is a refusal.
  if (response.status === 404 || response.status === 405) throw new StreamUnavailable("No stream route.");
  if (!response.ok) throw await refusal(response);

  let outcome = null;
  const take = (events) => {
    for (const e of events) {
      if (outcome) return;
      onEvent(e);
      if (e.event === "run.finished") outcome = { final: e.data.result };
      else if (e.event === "run.failed") outcome = { error: e.data.message || "The run failed." };
    }
  };

  const reader = response.body && typeof response.body.getReader === "function" ? response.body.getReader() : null;
  if (!reader) {
    // No reading as it arrives here: the run still ran once; read it whole.
    take(parseSse(`${await response.text()}\n\n`).events);
  } else {
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (!outcome) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = parseSse(buffer);
        buffer = rest;
        take(events);
      }
      if (!outcome && buffer.trim()) take(parseSse(`${buffer}\n\n`).events);
      if (outcome) reader.cancel().catch(() => {});
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      throw new StreamUnavailable(e && e.message ? e.message : "The stream broke.");
    }
  }
  if (!outcome) throw new StreamUnavailable("The stream ended before the run did.");
  if (outcome.error) throw new Error(outcome.error);
  return outcome.final;
}
