import { test } from "node:test";
import assert from "node:assert/strict";
import { modeStatus, replayNote } from "./status.js";

// Built here rather than written out, so no scanner mistakes it for a real key.
const KEY = ["sk", "proj", `status${"r".repeat(24)}91ad`].join("-");
const ANT = ["sk", "ant", `status${"r".repeat(24)}91ad`].join("-");
const LIMITS = { questions_per_minute: 6, questions_per_session: 30 };

test("nothing is said before the server has answered", () => {
  assert.equal(modeStatus(null, {}), null);
  assert.equal(modeStatus(undefined, KEY), null);
});

test("hosted with no key: a short pill, and the reason in full beside it", () => {
  const status = modeStatus({ hosted: true, limits: LIMITS }, {});
  assert.equal(status.tone, "warn");
  assert.equal(status.label, "Hosted demo · No key yet");
  assert.match(status.detail, /Settings/);
});

test("hosted with no key but recordings: the pill says the guided questions replay", () => {
  const status = modeStatus({ hosted: true, limits: LIMITS, recorded: ["a", "b"], questions: ["a", "b", "c"] }, {});
  assert.equal(status.tone, "warn");
  assert.equal(status.label, "Hosted demo · Recorded runs");
  assert.match(status.detail, /2 guided questions answer from recorded runs/);
  assert.match(status.detail, /Settings/);
});

test("hosted with one key names its provider, and the detail keeps the limits", () => {
  const status = modeStatus({ hosted: true, limits: LIMITS }, { openai: KEY });
  assert.equal(status.tone, "ok");
  assert.equal(status.label, "Hosted demo · OpenAI key in this tab");
  assert.match(status.detail, /6 questions a minute and 30 a session/);
  assert.match(status.detail, /under Settings\.$/);
});

test("hosted with keys for two providers counts them", () => {
  const status = modeStatus({ hosted: true, limits: LIMITS }, { openai: KEY, anthropic: ANT });
  assert.equal(status.label, "Hosted demo · 2 keys in this tab");
  assert.match(status.detail, /the 2 keys/);
});

test("replay says how much it can answer, and the full sentence stays in the detail", () => {
  const meta = { mode: "replay", recorded_questions: 12, questions: new Array(20).fill("q") };
  const status = modeStatus(meta, {}, { canSet: true });
  assert.equal(status.tone, "warn");
  assert.equal(status.label, "Replay mode · 12 of 20 recorded");
  assert.equal(status.detail, replayNote(12, 20, true));
  assert.match(status.detail, /add an API key under Settings/);
});

test("replay with no recordings says so in the pill", () => {
  const status = modeStatus({ mode: "replay", recorded_questions: 0, questions: [] }, {});
  assert.equal(status.label, "Replay mode · No recordings");
  assert.match(status.detail, /no question can be answered/);
  assert.match(status.detail, /restart with --api-key\.$/);
});

test("live mode is short and says where questions go", () => {
  const status = modeStatus({ mode: "live" }, {});
  assert.equal(status.tone, "ok");
  assert.equal(status.label, "Live · configured model");
  assert.equal(status.detail, "Questions go to the configured model.");
});
