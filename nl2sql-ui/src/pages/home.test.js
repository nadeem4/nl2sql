import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CLIP_ROOT, FEATURES, HERO, MORE, STEPS, clipFor, facts, sampleQuestion } from "./home.js";

test("the hero says what the product does, in the owner's words", () => {
  assert.equal(HERO.eyebrow, "Questions in, rows out");
  assert.equal(HERO.title, "Ask your database a question. See the plan before it becomes SQL.");
  assert.match(HERO.lede, /typed query plan/);
  assert.match(HERO.lede, /checks it against your schema/);
  assert.match(HERO.lede, /only then writes SQL/);
  assert.deepEqual(HERO.ctas.map((c) => c.label), ["Try a sample question", "Use your own key", "See how it works"]);
  assert.equal(HERO.ctas[1].href, "#/settings");
});

test("five steps, in the order a question travels, each marked model or code", () => {
  assert.deepEqual(STEPS.map((s) => s.name), ["Question", "Plan", "Check", "SQL", "Rows"]);
  // The plan is the model's; the checks and the SQL are code. That is the
  // claim the whole page rests on, and it is what the Pipeline page says too.
  assert.equal(STEPS.find((s) => s.name === "Plan").kind, "model");
  assert.equal(STEPS.find((s) => s.name === "Check").kind, "code");
  assert.equal(STEPS.find((s) => s.name === "SQL").kind, "code");
  for (const step of STEPS) assert.ok(step.what.length > 0 && step.what.length < 60, step.name);
});

test("three features, each opening its own page, each with a light and dark clip", () => {
  assert.deepEqual(FEATURES.map((f) => f.heading),
    ["Ask, and get rows back", "Watch the plan become SQL", "See what it looked up"]);
  assert.deepEqual(FEATURES.map((f) => f.href), ["#/ask", "#/pipeline", "#/retrieval"]);
  for (const f of FEATURES) {
    const clip = clipFor(f.id);
    assert.equal(clip.src, `${CLIP_ROOT}${f.id}.webm`);
    assert.equal(clip.srcDark, `${CLIP_ROOT}${f.id}-dark.webm`);
    assert.equal(clip.poster, `${CLIP_ROOT}${f.id}.jpg`);
    assert.equal(clip.posterDark, `${CLIP_ROOT}${f.id}-dark.jpg`);
    assert.ok(f.points.length === 2);
  }
});

test("the more band: run it locally, the docs and the source", () => {
  assert.deepEqual(MORE.map((m) => m.label), ["Run it on your machine", "Read the docs", "Source on GitHub"]);
  assert.match(MORE[0].code.join(" "), /pip install "nl2sql-engine\[demo\]"/);
  assert.ok(MORE[0].code.includes("nl2sql demo"));
  assert.equal(MORE[1].href, "https://nadeem4.github.io/nl2sql/");
  assert.equal(MORE[2].href, "https://github.com/nadeem4/nl2sql");
});

test("the sample question is one a visitor with no key can replay", () => {
  const meta = {
    questions: ["a", "b", "c"],
    question_groups: [{ datasource: "chinook", questions: ["a", "b", "c"] }],
    recorded: ["b", "c"],
  };
  assert.equal(sampleQuestion(meta), "b");
  // Nothing recorded: the first guided question, which asks for a key.
  assert.equal(sampleQuestion({ ...meta, recorded: [] }), "a");
  assert.equal(sampleQuestion({ questions: [] }), null);
  assert.equal(sampleQuestion(null), null);
});

test("the facts only promise what this server does", () => {
  const recorded = facts({ hosted: true, recorded: ["a"] });
  assert.deepEqual(recorded, ["No sign-in", "Sample databases included", "A key is only needed for your own questions"]);
  // Hosted with nothing recorded, every question needs a key: say that instead.
  assert.equal(facts({ hosted: true, recorded: [] })[2], "Bring your own API key; it stays in this tab");
  // Local replay with recordings answers the guided questions with no key too.
  assert.equal(facts({ mode: "replay", recorded_questions: 3 })[2], "A key is only needed for your own questions");
  assert.equal(facts({ mode: "live" })[2], "Runs on your machine, with your key");
  assert.deepEqual(facts(null).slice(0, 2), ["No sign-in", "Sample databases included"]);
});

test("no em dash anywhere in the home page's words", () => {
  const source = readFileSync(new URL("./home.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /[–—]/);
});
