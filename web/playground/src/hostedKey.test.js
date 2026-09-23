// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { KEY_HEADER, KEY_STORE, askHeaders, looksLikeKey, maskKey, readKey, writeKey } from "./hostedKey.js";

// Built here rather than written out, so no scanner mistakes it for a real key.
const KEY = ["sk", "proj", `hosted${"k".repeat(24)}4b7c`].join("-");

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

test("a key is kept in this tab's storage and read back", () => {
  const storage = fakeStorage();

  const saved = writeKey(storage, `  ${KEY}  `);

  assert.equal(saved, KEY);
  assert.equal(storage.store[KEY_STORE], KEY);
  assert.equal(readKey(storage), KEY);
});

test("clearing removes it rather than storing an empty value", () => {
  const storage = fakeStorage({ [KEY_STORE]: KEY });

  writeKey(storage, "");

  assert.equal(KEY_STORE in storage.store, false);
  assert.equal(readKey(storage), "");
});

test("storage that throws leaves the page working", () => {
  const storage = brokenStorage();

  assert.equal(readKey(storage), "");
  assert.equal(writeKey(storage, KEY), KEY);
});

test("the key travels as a header, never in the body", () => {
  const headers = askHeaders(KEY);

  assert.equal(headers[KEY_HEADER], KEY);
  assert.equal(headers["Content-Type"], "application/json");
});

test("no key means no header, so the server answers with its own sentence", () => {
  assert.deepEqual(askHeaders(""), { "Content-Type": "application/json" });
  assert.deepEqual(askHeaders(null), { "Content-Type": "application/json" });
});

test("a key is only ever shown masked", () => {
  assert.equal(maskKey(KEY), `sk-...${KEY.slice(-4)}`);
  assert.equal(maskKey("short"), "***");
  assert.equal(maskKey(""), "***");
});

test("the shape check matches the server's", () => {
  assert.equal(looksLikeKey(KEY), true);
  assert.equal(looksLikeKey("short"), false);
  assert.equal(looksLikeKey("has spaces in it and is long enough"), false);
  assert.equal(looksLikeKey("a".repeat(19)), false);
  assert.equal(looksLikeKey("a".repeat(20)), true);
});
