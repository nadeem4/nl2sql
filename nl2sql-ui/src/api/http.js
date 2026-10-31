// The one place the page talks to nl2sql-api (wave_d.md, section 5): `request` for JSON, `stream` for Server-Sent
// Events. Each area's client file (chat.js, dashboards.js, ...) is a thin set of calls to these two.
//
// Every call carries the tab's identity and keys, so no page has to remember to:
//   * `Authorization: Bearer <key>`, when the tab holds an API key (the server's `api_keys` mode; under `proxy` and
//     `none` there is no key and nothing is sent);
//   * `X-NL2SQL-Api-Key-<provider>` and `X-NL2SQL-Models`, from the keys and step models the tab holds (D210).
// A failure is always an `ApiError`, whether the server sent its error envelope, sent something else, or sent nothing.
import { askHeaders, readKeys } from "../pages/hostedKey.js";
import { readModels } from "../pages/hostedModels.js";
import { createSseParser } from "./sse.js";

export const BASE = "/api/v1";
// Where the tab's own API key lives: sessionStorage, so closing the tab drops it and nothing writes it to disk.
export const API_KEY_STORE = "nl2sql.ui.apiKey";

// The server's error envelope, `{error: {code, message, fix, details, request_id}}`, as an exception.
// `status` is 0 when the server could not be reached.
export class ApiError extends Error {
  constructor({ status, code, message, fix = null, details = [], requestId = null }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fix = fix;
    this.details = details;
    this.requestId = requestId;
  }
}

export function readApiKey(storage) {
  try {
    return (storage.getItem(API_KEY_STORE) || "").trim();
  } catch {
    return "";
  }
}

// Storage can be missing or throw (private windows, blocked site data); the key then simply is not kept.
export function writeApiKey(storage, key) {
  try {
    const value = (key || "").trim();
    if (value) storage.setItem(API_KEY_STORE, value);
    else storage.removeItem(API_KEY_STORE);
  } catch {
    // Not kept.
  }
}

async function failure(response) {
  let envelope = null;
  try {
    envelope = (await response.json()).error || null;
  } catch {
    // Not JSON: a proxy's page, an empty body.
  }
  const requestId = (envelope && envelope.request_id) || response.headers.get("X-Request-ID") || null;
  if (envelope && envelope.code) {
    return new ApiError({
      status: response.status, code: envelope.code, message: envelope.message,
      fix: envelope.fix ?? null, details: envelope.details || [], requestId,
    });
  }
  return new ApiError({
    status: response.status, code: `HTTP_${response.status}`,
    message: `The server answered ${response.status}${response.statusText ? ` ${response.statusText}` : ""}.`, requestId,
  });
}

export function createHttp({ fetch, storage }) {
  function headers(hasBody, accept) {
    const out = { Accept: accept };
    const sent = askHeaders(readKeys(storage), readModels(storage));
    delete sent["Content-Type"];
    Object.assign(out, sent);
    const key = readApiKey(storage);
    if (key) out.Authorization = `Bearer ${key}`;
    if (hasBody) out["Content-Type"] = "application/json";
    return out;
  }

  async function send(method, path, body, accept, signal) {
    const hasBody = body !== undefined && body !== null;
    const init = { method, headers: headers(hasBody, accept) };
    if (hasBody) init.body = JSON.stringify(body);
    if (signal) init.signal = signal;
    let response;
    try {
      response = await fetch(`${BASE}${path}`, init);
    } catch (cause) {
      if (cause && cause.name === "AbortError") throw cause;
      throw new ApiError({
        status: 0, code: "NETWORK", message: "The server could not be reached.",
        fix: "Check that nl2sql-api is running and that this page was opened from it.",
      });
    }
    if (!response.ok) throw await failure(response);
    return response;
  }

  return {
    // JSON in, JSON out. `body` is omitted for a read; a 204 answers null.
    async request(method, path, body) {
      const response = await send(method, path, body, "application/json");
      return response.status === 204 ? null : response.json();
    },

    // Server-Sent Events: `onEvent({event, data})` for each frame, `data` parsed as JSON when it is (the text
    // otherwise). A body makes it a POST, none a GET. Resolves when the server ends the stream.
    async stream(path, body, onEvent, signal) {
      const hasBody = body !== undefined && body !== null;
      const response = await send(hasBody ? "POST" : "GET", path, body, "text/event-stream", signal);
      const parser = createSseParser(({ event, data }) => {
        let parsed = data;
        try {
          parsed = JSON.parse(data);
        } catch {
          // Plain text.
        }
        onEvent({ event, data: parsed });
      });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parser.push(decoder.decode(value, { stream: true }));
      }
      parser.end();
    },
  };
}

// The page's own client: the browser's fetch and this tab's sessionStorage, looked up at call time.
const page = () => createHttp({
  fetch: (url, init) => globalThis.fetch(url, init),
  storage: globalThis.sessionStorage,
});

export const request = (method, path, body) => page().request(method, path, body);
export const stream = (path, body, onEvent, signal) => page().stream(path, body, onEvent, signal);
