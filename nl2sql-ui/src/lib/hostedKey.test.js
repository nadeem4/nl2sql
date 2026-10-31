// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  KEY_HEADER, KEY_STORE, MODELS_HEADER, askHeaders, keyStoreFor, looksLikeKey, maskKey,
  providerForKey, readKeys, writeKeyFor,
} from "./hostedKey.js";

// Built here rather than written out, so no scanner mistakes one for a real key.
const KEY = ["sk", "proj", `hosted${"k".repeat(24)}4b7c`].join("-");
const CLAUDE = ["sk", "ant", "api03", `hosted${"c".repeat(24)}9f2e`].join("-");
const ROUTER = ["sk", "or", "v1", `hosted${"r".repeat(24)}1a5d`].join("-");

function fakeStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = v;
    },
    removeItem: (k) => {
      delete store[k];
    },
    store,
  };
}

function brokenStorage() {
  return {
    getItem() {
      throw new Error("site data blocked");
    },
    setItem() {
      throw new Error("site data blocked");
    },
    removeItem() {
      throw new Error("site data blocked");
    },
  };
}

test("a key is kept per provider in this tab's storage and read back", () => {
  const storage = fakeStorage();

  const keys = writeKeyFor(storage, "openai", `  ${KEY}  `);

  assert.deepEqual(keys, { openai: KEY });
  assert.equal(storage.store[keyStoreFor("openai")], KEY);
  assert.deepEqual(readKeys(storage), { openai: KEY });
});

test("one provider's key does not disturb another's", () => {
  const storage = fakeStorage();

  writeKeyFor(storage, "openai", KEY);
  const both = writeKeyFor(storage, "anthropic", CLAUDE);

  assert.deepEqual(both, { openai: KEY, anthropic: CLAUDE });
  assert.deepEqual(readKeys(storage), { openai: KEY, anthropic: CLAUDE });
});

test("clearing one provider removes it and leaves the others", () => {
  const storage = fakeStorage();
  writeKeyFor(storage, "openai", KEY);
  writeKeyFor(storage, "anthropic", CLAUDE);

  const left = writeKeyFor(storage, "openai", "");

  assert.deepEqual(left, { anthropic: CLAUDE });
  assert.equal(keyStoreFor("openai") in storage.store, false);
});

test("a key left by the older single-key form moves to its provider's slot", () => {
  const storage = fakeStorage({ [KEY_STORE]: CLAUDE });

  const keys = readKeys(storage);

  assert.deepEqual(keys, { anthropic: CLAUDE });
  assert.equal(storage.store[keyStoreFor("anthropic")], CLAUDE);
  // And the old slot is emptied rather than left holding a key.
  assert.equal(KEY_STORE in storage.store, false);
});

test("storage that throws leaves the page working", () => {
  const storage = brokenStorage();

  assert.deepEqual(readKeys(storage), {});
  assert.deepEqual(writeKeyFor(storage, "openai", KEY), { openai: KEY });
});

test("each key travels in its own header, never in the body", () => {
  const headers = askHeaders({ openai: KEY, anthropic: CLAUDE });

  assert.equal(headers[`${KEY_HEADER}-openai`], KEY);
  assert.equal(headers[`${KEY_HEADER}-anthropic`], CLAUDE);
  assert.equal(headers["Content-Type"], "application/json");
  // With more than one, no header stands in for "the key".
  assert.equal(KEY_HEADER in headers, false);
  // And no header holds two of them.
  assert.equal(Object.values(headers).filter((v) => v.includes(KEY) && v.includes(CLAUDE)).length, 0);
});

test("one key also goes in the unnamed header, so the simple path is what it was", () => {
  const headers = askHeaders({ openrouter: ROUTER });

  assert.equal(headers[KEY_HEADER], ROUTER);
  assert.equal(headers[`${KEY_HEADER}-openrouter`], ROUTER);
});

test("a bare key is still accepted, and names its own provider", () => {
  const headers = askHeaders(KEY);

  assert.equal(headers[KEY_HEADER], KEY);
  assert.equal(headers[`${KEY_HEADER}-openai`], KEY);
});

test("no key means no key header, so the server answers with its own sentence", () => {
  assert.deepEqual(askHeaders({}), { "Content-Type": "application/json" });
  assert.deepEqual(askHeaders(null), { "Content-Type": "application/json" });
  assert.deepEqual(askHeaders(""), { "Content-Type": "application/json" });
});

test("the step choices travel in their own header and carry no key", () => {
  const headers = askHeaders({ openai: KEY, anthropic: CLAUDE },
    { astplanner: "anthropic:claude-opus-5" });

  assert.equal(headers[MODELS_HEADER], '{"astplanner":"anthropic:claude-opus-5"}');
  assert.equal(headers[MODELS_HEADER].includes(KEY), false);
  assert.equal(headers[MODELS_HEADER].includes(CLAUDE), false);
});

test("no choices means no model header at all", () => {
  assert.equal(MODELS_HEADER in askHeaders({ openai: KEY }, {}), false);
  assert.equal(MODELS_HEADER in askHeaders({ openai: KEY }), false);
});

test("a key is only ever shown masked", () => {
  assert.equal(maskKey(KEY), `sk-...${KEY.slice(-4)}`);
  assert.equal(maskKey("short"), "***");
  assert.equal(maskKey(""), "***");
});

test("a key names its provider the way the server reads it", () => {
  assert.equal(providerForKey(KEY), "openai");
  assert.equal(providerForKey(CLAUDE), "anthropic");
  assert.equal(providerForKey(ROUTER), "openrouter");
  assert.equal(providerForKey(""), "openai");
});

test("the shape check matches the server's", () => {
  assert.equal(looksLikeKey(KEY), true);
  assert.equal(looksLikeKey("short"), false);
  assert.equal(looksLikeKey("has spaces in it and is long enough"), false);
  assert.equal(looksLikeKey("a".repeat(19)), false);
  assert.equal(looksLikeKey("a".repeat(20)), true);
});
