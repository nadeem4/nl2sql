import { test } from "node:test";
import assert from "node:assert/strict";
import { NO_KEY_REASON, needsKey } from "./firstRun.js";

// Built here rather than written out, so no scanner mistakes it for a real key.
const KEY = ["sk", "proj", `first${"r".repeat(24)}91ad`].join("-");

test("the hosted demo asks for a key before it takes a question", () => {
  assert.equal(needsKey({ hosted: true }, ""), true);
  assert.equal(needsKey({ hosted: true }, null), true);
  assert.equal(needsKey({ hosted: true }, "   "), true);
});

test("a key saved in this tab clears the state, with no reload", () => {
  assert.equal(needsKey({ hosted: true }, KEY), false);
  assert.equal(needsKey({ hosted: true }, `  ${KEY}  `), false);
});

test("local mode never shows it, key or no key", () => {
  assert.equal(needsKey({ hosted: false, mode: "replay" }, ""), false);
  assert.equal(needsKey({ mode: "live" }, ""), false);
  assert.equal(needsKey({ mode: "replay" }, KEY), false);
});

test("nothing is claimed before the server has answered", () => {
  assert.equal(needsKey(null, ""), false);
  assert.equal(needsKey(undefined, KEY), false);
});

test("the reason names where the key goes, so a disabled control can carry it", () => {
  assert.match(NO_KEY_REASON, /Settings/);
});
