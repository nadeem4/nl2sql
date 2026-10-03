// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { SETTINGS_ROUTE, describeFault } from "./faults.js";

function entry(error_code, extra = {}) {
  return {
    node: "datasource_resolver", message: "engine message", error_code, severity: "ERROR",
    provider: "OpenAI", detail: "HTTP 401 (invalid_api_key): Incorrect API key provided: [redacted key].", ...extra,
  };
}

test("a rejected key names the provider, says nothing ran, and leads to Settings", () => {
  assert.deepEqual(describeFault(entry("PROVIDER_AUTH_FAILED")), {
    headline: "OpenAI rejected this key.",
    body: "Nothing was planned or run, and nothing was charged.",
    action: { label: "Replace the key", route: SETTINGS_ROUTE },
  });
  assert.equal(SETTINGS_ROUTE, "#/settings");
});

test("a key rejected after the plan ran does not claim nothing ran", () => {
  const fault = describeFault(entry("PROVIDER_AUTH_FAILED", { node: "answer_synthesizer", provider: "Anthropic" }));
  assert.equal(fault.headline, "Anthropic rejected this key.");
  assert.doesNotMatch(fault.body, /Nothing was planned/);
});

test("rate limited asks for a minute's wait and offers no action", () => {
  assert.deepEqual(describeFault(entry("PROVIDER_RATE_LIMITED")), {
    headline: "Rate limited.",
    body: "Wait a minute and ask again.",
    action: null,
  });
});

test("every provider code has its own headline and body", () => {
  const codes = ["PROVIDER_AUTH_FAILED", "PROVIDER_RATE_LIMITED", "PROVIDER_QUOTA_EXCEEDED", "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE", "PROVIDER_MODEL_UNAVAILABLE"];
  const headlines = new Set();
  for (const code of codes) {
    const fault = describeFault(entry(code));
    assert.ok(fault.headline && fault.body, code);
    assert.notEqual(fault.body, "engine message", code);
    headlines.add(fault.headline);
  }
  assert.equal(headlines.size, codes.length);
});

test("a model the key may not use leads to Settings to pick another", () => {
  const fault = describeFault(entry("PROVIDER_MODEL_UNAVAILABLE"));
  assert.match(fault.headline, /^OpenAI /);
  assert.equal(fault.action.route, SETTINGS_ROUTE);
});

test("an unnamed provider still reads as a sentence", () => {
  const fault = describeFault(entry("PROVIDER_AUTH_FAILED", { provider: null }));
  assert.equal(fault.headline, "The model provider rejected this key.");
});

test("an unknown code falls back to the engine's message", () => {
  assert.deepEqual(describeFault(entry("PLANNING_FAILURE", { message: "Planner failed.", provider: null })), {
    headline: "The run stopped.",
    body: "Planner failed.",
    action: null,
  });
});

test("no entry, no fault", () => {
  assert.equal(describeFault(null), null);
  assert.equal(describeFault(undefined), null);
});
