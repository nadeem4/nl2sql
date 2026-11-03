// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { modelChanges, modelGroups, parseRoles, providerName, serverRows, stepRows } from "./settings.js";

// The shapes GET /llm/providers and GET /llm answer (engine.llm.providers(), engine.llm.list()).
const PROVIDERS = [
  { name: "openai", label: "OpenAI", key_variable: "OPENAI_API_KEY", key_present: true, models: ["gpt-5.4", "gpt-4.1"],
    key_shape: { pattern: "^[A-Za-z0-9_-]{20,}$", min_length: 20, prefix: null } },
  { name: "anthropic", label: "Anthropic", key_variable: "ANTHROPIC_API_KEY", key_present: false, models: ["claude-opus-5"],
    key_shape: { pattern: "^[A-Za-z0-9_-]{20,}$", min_length: 20, prefix: "sk-ant-" } },
  { name: "ollama", label: "Ollama", key_variable: null, key_present: false, models: [], key_shape: null },
];
const LLMS = {
  default: { provider: "openai", model: "gpt-5.4", temperature: 0, inherited: false },
  decomposer: { provider: "openai", model: "gpt-5.4", temperature: 0, inherited: true },
  ast_planner: { provider: "anthropic", model: "claude-opus-5", temperature: null, inherited: false },
  mystery_agent: { provider: "openai", model: "gpt-5.4", temperature: 0, inherited: true },
};

test("modelGroups starts with the default, then one group per provider that has models", () => {
  const groups = modelGroups(PROVIDERS, "gpt-5.4");
  assert.deepEqual(groups[0], { label: null, options: [{ value: "", label: "Default (gpt-5.4)" }] });
  assert.deepEqual(groups.map((g) => g.label), [null, "OpenAI", "Anthropic"]);
  assert.deepEqual(groups[1].options, [
    { value: "openai:gpt-5.4", label: "gpt-5.4" }, { value: "openai:gpt-4.1", label: "gpt-4.1" },
  ]);
});

test("modelGroups with no providers offers only the default", () => {
  assert.deepEqual(modelGroups([], "llama3.1"), [{ label: null, options: [{ value: "", label: "Default (llama3.1)" }] }]);
});

test("stepRows names each agent the engine lists, in plain words, without the default", () => {
  const rows = stepRows(LLMS);
  assert.deepEqual(rows.map((r) => r.agent), ["decomposer", "ast_planner", "mystery_agent"]);
  assert.equal(rows[1].label, "Query planner");
  assert.ok(rows[1].does.length > 10);
  // An agent the page has no words for reads as its own name.
  assert.deepEqual([rows[2].label, rows[2].does], ["mystery_agent", ""]);
});

test("serverRows lists default first and says what each agent inherits", () => {
  const rows = serverRows(LLMS);
  assert.equal(rows[0].agent, "default");
  assert.equal(rows[0].value, "openai:gpt-5.4");
  assert.equal(rows.find((r) => r.agent === "decomposer").inherited, true);
  assert.equal(rows.find((r) => r.agent === "ast_planner").value, "anthropic:claude-opus-5");
});

test("modelGroups can mark a provider whose key the server does not have", () => {
  const groups = modelGroups(PROVIDERS, "gpt-5.4", { markMissingKeys: true });
  assert.deepEqual(groups.map((g) => g.label), [null, "OpenAI", "Anthropic (no key)"]);
  assert.deepEqual(modelGroups(PROVIDERS, "gpt-5.4").map((g) => g.label), [null, "OpenAI", "Anthropic"]);
});

test("modelChanges sends only the edits, as provider and model", () => {
  assert.deepEqual(modelChanges(LLMS, {}), {});
  assert.deepEqual(modelChanges(LLMS, { default: "openai:gpt-4.1", decomposer: "anthropic:claude-opus-5" }), {
    default: { provider: "openai", model: "gpt-4.1" },
    decomposer: { provider: "anthropic", model: "claude-opus-5" },
  });
});

test("choosing the default for an agent that had its own model drops it; one that inherited is left alone", () => {
  assert.deepEqual(modelChanges(LLMS, { ast_planner: "", decomposer: "" }), { ast_planner: null });
});

test("parseRoles splits on commas and spaces and drops empties", () => {
  assert.deepEqual(parseRoles(" analyst, admin  viewer,, "), ["analyst", "admin", "viewer"]);
  assert.deepEqual(parseRoles(""), []);
});

test("providerName writes each provider the way its own documentation does", () => {
  assert.equal(providerName("openai"), "OpenAI");
  assert.equal(providerName("anthropic"), "Anthropic");
  assert.equal(providerName("openrouter"), "OpenRouter");
  assert.equal(providerName("vllm"), "vllm");
  assert.equal(providerName(null), "");
});
