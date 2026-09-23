import { test } from "node:test";
import assert from "node:assert/strict";
import { NO_KEY_REASON, hostedNote, needsKey } from "./firstRun.js";

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

test("the mode line says nothing while the first-run state is saying it", () => {
  const meta = { hosted: true, limits: { questions_per_minute: 6, questions_per_session: 30 } };
  assert.equal(hostedNote(meta, ""), "");
  assert.equal(hostedNote(meta, "   "), "");
  assert.equal(hostedNote(null, ""), "");
});

test("with a key the mode line has something of its own to say, limits included", () => {
  const meta = { hosted: true, limits: { questions_per_minute: 6, questions_per_session: 30 } };
  const line = hostedNote(meta, KEY);
  assert.match(line, /^Questions run on the key in this browser tab/);
  assert.match(line, /6 questions a minute and 30 a session/);
  assert.match(line, /Replace or clear it under$/);
});

test("a server that reports no limits has none to quote", () => {
  assert.doesNotMatch(hostedNote({ hosted: true }, KEY), /a minute/);
});
