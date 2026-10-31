import { test } from "node:test";
import assert from "node:assert/strict";
import { guidedGroups } from "./questions.js";

test("guided questions come back grouped by the datasource they ask about", () => {
  const meta = {
    dataset: "chinook",
    questions: ["c1", "c2", "s1"],
    question_groups: [
      { datasource: "chinook", questions: ["c1", "c2"] },
      { datasource: "support", questions: ["s1"] },
    ],
  };
  assert.deepEqual(guidedGroups(meta), [
    { datasource: "chinook", questions: ["c1", "c2"] },
    { datasource: "support", questions: ["s1"] },
  ]);
});

test("a group with no questions is not a pile worth labelling", () => {
  const meta = {
    dataset: "chinook",
    questions: ["c1"],
    question_groups: [
      { datasource: "chinook", questions: ["c1"] },
      { datasource: "support", questions: [] },
      { datasource: "webanalytics" },
    ],
  };
  assert.deepEqual(guidedGroups(meta), [{ datasource: "chinook", questions: ["c1"] }]);
});

test("a server that sends no groups still gets its questions shown, under the dataset", () => {
  assert.deepEqual(guidedGroups({ dataset: "chinook", questions: ["c1", "c2"] }),
                   [{ datasource: "chinook", questions: ["c1", "c2"] }]);
  assert.deepEqual(guidedGroups({ dataset: "chinook", questions: ["c1"], question_groups: [] }),
                   [{ datasource: "chinook", questions: ["c1"] }]);
});

test("no questions and no meta mean nothing to offer", () => {
  assert.deepEqual(guidedGroups(null), []);
  assert.deepEqual(guidedGroups({ dataset: "chinook", questions: [] }), []);
  assert.deepEqual(guidedGroups({ dataset: "chinook" }), []);
});

test("one database needs no labels; several do", () => {
  const one = guidedGroups({ dataset: "chinook", questions: ["c1"] });
  assert.equal(one.length, 1);
  const several = guidedGroups({
    dataset: "chinook",
    questions: ["c1", "s1"],
    question_groups: [
      { datasource: "chinook", questions: ["c1"] },
      { datasource: "support", questions: ["s1"] },
    ],
  });
  assert.equal(several.length, 2);
});
