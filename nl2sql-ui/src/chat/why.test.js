import { test } from "node:test";
import assert from "node:assert/strict";
import { whySections } from "./why.js";

test("no explanation gives no sections", () => {
  assert.deepEqual(whySections(null), []);
  assert.deepEqual(whySections({ explanation: null }), []);
});

test("each step becomes labelled lines; empty parts are left out", () => {
  const explanation = {
    steps: [{
      source: "generated", tables: ["Customer"], joins: [], filters: ["Country is USA"], groupings: [],
      measures: ["count of customers"], order: [],
      terms: [{ term: "customer", definition: "a buyer", source: "glossary" }],
      rules: ["rows are capped"], assumptions: ["current year"], planner_note: "I used Customer.",
    }],
  };
  const [s] = whySections({ explanation });
  assert.deepEqual(s.parts.map((p) => p.label), ["Tables", "Filters", "Measures", "Terms", "Rules", "Assumptions", "Planner note"]);
  assert.equal(s.parts.find((p) => p.label === "Terms").items[0], "customer: a buyer (glossary)");
  assert.equal(s.parts.find((p) => p.label === "Planner note").model, true);
  assert.equal(s.parts.find((p) => p.label === "Tables").model, false);
});
