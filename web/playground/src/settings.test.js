// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { changedModels, choicesFrom, modelOptions, variableModels } from "./settings.js";

const MODELS = [
  { id: "gpt-5.4", temperature: 0 },
  { id: "gpt-4.1", temperature: 0 },
  { id: "gpt-5.5", temperature: null },
];

const NODES = [
  { agent: "decomposer", model: null },
  { agent: "astplanner", model: "gpt-4.1" },
  { agent: "refiner", model: null },
];

test("modelOptions starts with the default and lists every verified model once", () => {
  assert.deepEqual(modelOptions(MODELS, "gpt-5.4"), [
    { value: "", label: "Default (gpt-5.4)" },
    { value: "gpt-5.4", label: "gpt-5.4" },
    { value: "gpt-4.1", label: "gpt-4.1" },
    { value: "gpt-5.5", label: "gpt-5.5, default temperature" },
  ]);
});

test("modelOptions with no verified list offers only the default", () => {
  assert.deepEqual(modelOptions([], "llama3.1"), [{ value: "", label: "Default (llama3.1)" }]);
});

test("choicesFrom maps each node to its model, or empty for the default", () => {
  assert.deepEqual(choicesFrom(NODES), { decomposer: "", astplanner: "gpt-4.1", refiner: "" });
});

test("changedModels sends only what differs, with null meaning the default", () => {
  const choices = { decomposer: "gpt-5.5", astplanner: "", refiner: "" };
  assert.deepEqual(changedModels(NODES, choices), { decomposer: "gpt-5.5", astplanner: null });
  assert.deepEqual(changedModels(NODES, choicesFrom(NODES)), {});
});

test("variableModels names the chosen models that run without a temperature", () => {
  assert.deepEqual(variableModels(MODELS, { decomposer: "gpt-5.5", astplanner: "gpt-5.5", refiner: "gpt-4.1" }), ["gpt-5.5"]);
  assert.deepEqual(variableModels(MODELS, { decomposer: "", astplanner: "gpt-4.1" }), []);
});
