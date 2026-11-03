// Pure helpers for the Settings page. The providers and their models live on the server (GET /llm/providers) and the
// agents and what each runs on in GET /llm; nothing here names a model.

// How each provider is written on the page. The ids are the server's; anything it sends that is not listed reads as
// itself.
export const PROVIDER_NAMES = { openai: "OpenAI", openrouter: "OpenRouter", anthropic: "Anthropic", ollama: "Ollama" };

export function providerName(id) {
  return PROVIDER_NAMES[id] || id || "";
}

// What each agent is called and does, in plain words. An agent not listed reads as its own name.
export const AGENTS = {
  decomposer: ["Question splitter", "Breaks a question into the parts that can be answered separately."],
  ast_planner: ["Query planner", "Turns a part into a typed plan of tables, columns, filters and joins."],
  table_selector: ["Table picker", "Picks the tables a large schema needs for the question."],
  synthesizer: ["Answer writer", "Writes the answer in words from the rows that came back."],
  faithfulness: ["Answer checker", "Checks every figure in the answer against the rows."],
  chat: ["Chat", "Decides what to do next in a conversation and calls the tools."],
  clarifier: ["Clarifier", "Asks back when a question is too vague to plan."],
  profiler: ["Profiler", "Drafts descriptions for tables and columns."],
};

const joined = (provider, model) => `${provider}:${model}`;

function split(value) {
  const at = value.indexOf(":");
  return [value.slice(0, at), value.slice(at + 1)];
}

// The groups for one selector: the default first, then one group per provider that lists models.
// With `markMissingKeys`, a provider whose key this server lacks is labelled "(no key)": choosing it would be refused.
export function modelGroups(providers, defaultModel, { markMissingKeys = false } = {}) {
  return [
    { label: null, options: [{ value: "", label: `Default (${defaultModel})` }] },
    ...(providers || []).filter((p) => p.models && p.models.length).map((p) => ({
      label: markMissingKeys && p.key_variable && !p.key_present ? `${p.label} (no key)` : p.label,
      options: p.models.map((m) => ({ value: joined(p.name, m), label: m })),
    })),
  ];
}

// The agents of GET /llm other than the default, as rows for a selector.
export function stepRows(llms) {
  return Object.keys(llms || {}).filter((agent) => agent !== "default").map((agent) => {
    const [label, does] = AGENTS[agent] || [agent, ""];
    return { agent, label, does };
  });
}

// Every row of GET /llm, the default first, with the choice as one "provider:model" value.
export function serverRows(llms) {
  const all = llms || {};
  const agents = Object.keys(all).filter((agent) => agent !== "default");
  return ["default", ...agents].filter((agent) => all[agent]).map((agent) => ({
    agent, value: joined(all[agent].provider, all[agent].model), inherited: Boolean(all[agent].inherited),
  }));
}

// The changes PATCH /settings/llm takes, from the edits (agent -> "provider:model", or "" to inherit the default): only
// what changed. An agent that already inherits stays so; the default has no "inherit".
export function modelChanges(llms, edits) {
  const changes = {};
  for (const [agent, value] of Object.entries(edits)) {
    if (value) {
      const [provider, model] = split(value);
      changes[agent] = { provider, model };
    } else if (agent !== "default" && llms[agent] && !llms[agent].inherited) {
      changes[agent] = null;
    }
  }
  return changes;
}

// "analyst, admin viewer" -> ["analyst", "admin", "viewer"].
export const parseRoles = (text) => String(text || "").split(/[\s,]+/).filter(Boolean);
