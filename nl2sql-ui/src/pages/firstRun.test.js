import { test } from "node:test";
import assert from "node:assert/strict";
import {
  NO_KEY_REASON, firstRunCopy, hostedNote, isRecorded, lockControls, missPrompt, needsKey, recordedBadge,
} from "./firstRun.js";

// Built here rather than written out, so no scanner mistakes it for a real key.
const HOSTED = { mode: "hosted" };
const withRecorded = (...recorded) => ({ mode: "hosted", datasources: [{ id: "c", recorded }] });
const KEY = ["sk", "proj", `first${"r".repeat(24)}91ad`].join("-");

test("the hosted demo asks for a key before it takes a question", () => {
  assert.equal(needsKey(HOSTED, ""), true);
  assert.equal(needsKey(HOSTED, null), true);
  assert.equal(needsKey(HOSTED, "   "), true);
});

test("a key saved in this tab clears the state, with no reload", () => {
  assert.equal(needsKey(HOSTED, KEY), false);
  assert.equal(needsKey(HOSTED, `  ${KEY}  `), false);
});

test("local mode never shows it, key or no key", () => {
  assert.equal(needsKey({ mode: "demo" }, ""), false);
  assert.equal(needsKey({ mode: "your_data" }, ""), false);
  assert.equal(needsKey({ mode: "demo" }, KEY), false);
});

test("a key for any provider clears it, since any one of them can answer", () => {
  assert.equal(needsKey(HOSTED, {}), true);
  assert.equal(needsKey(HOSTED, { openai: "" }), true);
  assert.equal(needsKey(HOSTED, { anthropic: KEY }), false);
  assert.equal(needsKey(HOSTED, { openai: KEY, anthropic: KEY }), false);
});

test("nothing is claimed before the server has answered", () => {
  assert.equal(needsKey(null, ""), false);
  assert.equal(needsKey(undefined, KEY), false);
});

test("the reason names where the key goes, so a disabled control can carry it", () => {
  assert.match(NO_KEY_REASON, /Settings/);
});

test("the mode line says nothing while the first-run state is saying it", () => {
  const meta = { mode: "hosted", limits: { questions_per_minute: 6, questions_per_session: 30 } };
  assert.equal(hostedNote(meta, ""), "");
  assert.equal(hostedNote(meta, "   "), "");
  assert.equal(hostedNote(null, ""), "");
});

test("with a key the mode line has something of its own to say, limits included", () => {
  const meta = { mode: "hosted", limits: { questions_per_minute: 6, questions_per_session: 30 } };
  const line = hostedNote(meta, KEY);
  assert.match(line, /^Questions run on the key in this browser tab/);
  assert.match(line, /6 questions a minute and 30 a session/);
  assert.match(line, /Replace or clear it, or change the model each step uses, under$/);
});

test("with a key per provider it counts them, and still quotes the limits", () => {
  const meta = { mode: "hosted", limits: { questions_per_minute: 6, questions_per_session: 30 } };
  const line = hostedNote(meta, { openai: KEY, anthropic: KEY });
  assert.match(line, /^Questions run on the 2 keys in this browser tab/);
  assert.match(line, /6 questions a minute and 30 a session/);
  assert.match(line, /Replace or clear them, or change the model each step uses, under$/);
  // An empty set is the same as no key at all.
  assert.equal(hostedNote(meta, {}), "");
  assert.equal(hostedNote(meta, { openai: "  " }), "");
});

test("a keyless visitor is never locked out: the controls stay open", () => {
  // `needsKey` says the first-run block is shown; it no longer disables anything.
  assert.equal(lockControls(HOSTED, {}), false);
  assert.equal(lockControls(withRecorded(), ""), false);
});

test("the guided questions a keyless visitor can replay", () => {
  const meta = withRecorded("How many customers are there?");
  assert.equal(isRecorded(meta, "How many customers are there?"), true);
  assert.equal(isRecorded(meta, "  How many customers are there?  "), true);
  assert.equal(isRecorded(meta, "Something else"), false);
  assert.equal(isRecorded(HOSTED, "anything"), false);
  assert.equal(isRecorded(null, "anything"), false);
});

test("the first-run pitch points at the recordings only when there are some", () => {
  const some = firstRunCopy(withRecorded("a", "b"));
  assert.match(some.why, /recorded run/i);
  assert.match(some.why, /your own question/i);
  assert.equal(some.keyOpen, false);
  const none = firstRunCopy(withRecorded());
  assert.doesNotMatch(none.why, /recorded/i);
  assert.match(none.why, /key/);
  assert.equal(none.keyOpen, true);
});

test("a replay miss on the hosted demo asks for a key, with the way to Settings", () => {
  const miss = Object.assign(new Error("No recorded answer"), { code: "REPLAY_MISS" });
  const prompt = missPrompt(miss, HOSTED);
  assert.match(prompt.text, /recorded/);
  assert.equal(prompt.action.route, "#/settings");
  assert.match(prompt.action.label, /key/i);
  // Elsewhere there is no tab-held key, so it only says what happened.
  assert.equal(missPrompt(miss, { mode: "demo" }).action, null);
  assert.equal(missPrompt(Object.assign(new Error("x"), { code: "TIMEOUT" }), HOSTED), null);
  assert.equal(missPrompt(null, HOSTED), null);
});

// What a replayed run reports: the recording's model on every call, and no token usage (G2b).
const WITH = { provider: "anthropic", model: "claude-test" };
const call = (extra) => ({ node: "astplanner", model: "claude-test", usage_reported: false, ...extra });

test("a run whose every call is the recording's model with no usage is a recorded run", () => {
  assert.equal(recordedBadge({ usage: { calls: [call(), call()] } }, WITH).label, "Recorded run");
  assert.match(recordedBadge({ usage: { calls: [call()] } }, WITH).title, /not run just now/);
});

test("a live run, or one with no recording to match, carries no badge", () => {
  assert.equal(recordedBadge({ usage: { calls: [call({ usage_reported: true })] } }, WITH), null);
  assert.equal(recordedBadge({ usage: { calls: [call(), call({ model: "gpt-x" })] } }, WITH), null);
  assert.equal(recordedBadge({ usage: { calls: [] } }, WITH), null);
  assert.equal(recordedBadge({ usage: { calls: [call()] } }, null), null);
  assert.equal(recordedBadge(null, WITH), null);
});

test("a server that reports no limits has none to quote", () => {
  assert.doesNotMatch(hostedNote(HOSTED, KEY), /a minute/);
});
