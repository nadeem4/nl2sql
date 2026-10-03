// Run with `npm test`. What the Settings key card shows: the masked tail of a
// key, which provider each card says it holds, and a key pasted under the
// wrong provider.
import { test } from "node:test";
import assert from "node:assert/strict";
import { keyMismatch, keyTail, providerCards } from "./hostedKey.js";

test("keyTail shows the last four characters only, never the prefix", () => {
  assert.equal(keyTail("sk-proj-abcdefghijklmnop0f3a"), "…0f3a");
  assert.equal(keyTail("  sk-ant-abcdefghijklmnop1234 "), "…1234");
  // Too short to mask usefully: nothing of it is shown.
  assert.equal(keyTail("sk-short"), null);
  assert.equal(keyTail(""), null);
  assert.equal(keyTail(null), null);
});

test("keyMismatch names the provider a key belongs to when another is chosen", () => {
  const openai = "sk-proj-" + "a".repeat(30);
  const anthropic = "sk-ant-" + "a".repeat(30);
  const openrouter = "sk-or-" + "a".repeat(30);

  assert.equal(keyMismatch("openai", openai), null);
  assert.equal(keyMismatch("anthropic", anthropic), null);
  assert.equal(keyMismatch("openrouter", openrouter), null);
  assert.equal(keyMismatch("openai", anthropic), "anthropic");
  assert.equal(keyMismatch("anthropic", openai), "openai");
  assert.equal(keyMismatch("openrouter", openai), "openai");
  assert.equal(keyMismatch("openai", ""), null);
});

test("providerCards lists every provider a key can be kept for, saying which hold one", () => {
  const cards = providerCards({ anthropic: "sk-ant-" + "b".repeat(30) });

  assert.deepEqual(cards.map((c) => c.id), ["openai", "anthropic", "openrouter"]);
  assert.deepEqual(cards.map((c) => c.label), ["OpenAI", "Anthropic", "OpenRouter"]);
  assert.deepEqual(cards.map((c) => c.held), [false, true, false]);
  assert.deepEqual(providerCards(null).map((c) => c.held), [false, false, false]);
});
