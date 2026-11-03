import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { guidedGroups } from "./questions.js";

const META = JSON.parse(readFileSync(new URL("../api/fixtures/Meta.json", import.meta.url), "utf8"));
const ds = (id, sample_questions) => ({ id, description: "", sample_questions, recorded: [] });

test("guided questions come back grouped by the datasource they ask about", () => {
  const meta = { datasources: [ds("chinook", ["c1", "c2"]), ds("support", ["s1"])] };
  assert.deepEqual(guidedGroups(meta), [
    { datasource: "chinook", questions: ["c1", "c2"] },
    { datasource: "support", questions: ["s1"] },
  ]);
});

test("a datasource with no questions is not a pile worth labelling", () => {
  const meta = { datasources: [ds("chinook", ["c1"]), ds("support", []), { id: "web" }] };
  assert.deepEqual(guidedGroups(meta), [{ datasource: "chinook", questions: ["c1"] }]);
});

test("the /meta fixture's questions come through", () => {
  assert.deepEqual(guidedGroups(META), [{ datasource: "chinook", questions: ["How many customers?"] }]);
});

test("no questions and no meta mean nothing to offer", () => {
  assert.deepEqual(guidedGroups(null), []);
  assert.deepEqual(guidedGroups({}), []);
  assert.deepEqual(guidedGroups({ datasources: [] }), []);
});
