// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { changedModels, choicesFrom, choiceValue, modelGroups, variableModels } from "./settings.js";

const PROVIDERS = [
  { id: "openai", label: "OpenAI", usable: true, models: [
    { id: "gpt-5.4", temperature: 0 },
    { id: "gpt-4.1", temperature: 0 },
    { id: "gpt-5.5", temperature: null },
  ] },
  { id: "anthropic", label: "Anthropic", usable: false, models: [
    { id: "claude-opus-5", temperature: null },
    { id: "claude-haiku-4-5", temperature: 0 },
  ] },
];

const NODES = [
  { agent: "decomposer", provider: null, model: null },
  { agent: "astplanner", provider: "openai", model: "gpt-4.1" },
  { agent: "refiner", provider: null, model: null },
];

test("modelGroups starts with the default, then one group per provider", () => {
  const groups = modelGroups(PROVIDERS, "gpt-5.4");

  assert.deepEqual(groups[0], { label: null, disabled: false, options: [{ value: "", label: "Default (gpt-5.4)" }] });
  assert.deepEqual(groups[1], { label: "OpenAI", disabled: false, options: [
    { value: "openai:gpt-5.4", label: "gpt-5.4" },
    { value: "openai:gpt-4.1", label: "gpt-4.1" },
    { value: "openai:gpt-5.5", label: "gpt-5.5, default temperature" },
  ] });
});

test("modelGroups shows a provider with no key, but it cannot be chosen", () => {
  const anthropic = modelGroups(PROVIDERS, "gpt-5.4")[2];

  assert.equal(anthropic.label, "Anthropic (no key)");
  assert.equal(anthropic.disabled, true);
  assert.deepEqual(anthropic.options.map((o) => o.value), ["anthropic:claude-opus-5", "anthropic:claude-haiku-4-5"]);
});

test("modelGroups with no providers offers only the default", () => {
  assert.deepEqual(modelGroups([], "llama3.1"), [
    { label: null, disabled: false, options: [{ value: "", label: "Default (llama3.1)" }] },
  ]);
});

test("choicesFrom maps each step to provider:model, or empty for the default", () => {
  assert.deepEqual(choicesFrom(NODES, "openai"), { decomposer: "", astplanner: "openai:gpt-4.1", refiner: "" });
});

test("a step saved without a provider is on the default's provider", () => {
  assert.deepEqual(choicesFrom([{ agent: "refiner", provider: null, model: "gpt-4.1" }], "openai"),
                   { refiner: "openai:gpt-4.1" });
});

test("changedModels sends a provider and a model, with null meaning the default", () => {
  const choices = { decomposer: "anthropic:claude-opus-5", astplanner: "", refiner: "" };

  assert.deepEqual(changedModels(NODES, choices, "openai"), {
    decomposer: { provider: "anthropic", model: "claude-opus-5" },
    astplanner: null,
  });
  assert.deepEqual(changedModels(NODES, choicesFrom(NODES, "openai"), "openai"), {});
});

test("variableModels names the chosen models that run without a temperature", () => {
  assert.deepEqual(variableModels(PROVIDERS, {
    decomposer: "openai:gpt-5.5", astplanner: "anthropic:claude-opus-5", refiner: "openai:gpt-4.1",
  }), ["gpt-5.5", "claude-opus-5"]);
  assert.deepEqual(variableModels(PROVIDERS, { decomposer: "", astplanner: "openai:gpt-4.1" }), []);
});

test("choiceValue is empty for the default", () => {
  assert.equal(choiceValue("openai", null), "");
  assert.equal(choiceValue("anthropic", "claude-opus-5"), "anthropic:claude-opus-5");
});
