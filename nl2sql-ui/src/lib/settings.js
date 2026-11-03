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
export function modelGroups(providers, defaultModel) {
  return [
    { label: null, options: [{ value: "", label: `Default (${defaultModel})` }] },
    ...(providers || []).filter((p) => p.models && p.models.length).map((p) => ({
      label: p.label,
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

// Each key points at its provider's environment variable, never at a value.
function keyFor(providers, name) {
  const found = (providers || []).find((p) => p.name === name);
  return found && found.key_variable ? { api_key: "${env:" + found.key_variable + "}" } : {};
}

// The `llm` section PUT /settings/llm replaces, built from GET /llm and the edits (agent -> "provider:model", or ""
// to inherit). Agents that inherit are left out. A changed default drops its temperature, which the new model may
// not accept.
export function llmSection(llms, providers, edits) {
  const base = llms.default;
  const chosen = "default" in edits && edits.default ? edits.default : joined(base.provider, base.model);
  const [provider, model] = split(chosen);
  const section = { provider, model };
  if (base.temperature !== null && base.temperature !== undefined && provider === base.provider && model === base.model) {
    section.temperature = base.temperature;
  }
  Object.assign(section, keyFor(providers, provider));
  const agents = {};
  for (const agent of Object.keys(llms)) {
    if (agent === "default") continue;
    const row = llms[agent];
    const value = agent in edits ? edits[agent] : (row.inherited ? "" : joined(row.provider, row.model));
    if (!value) continue;
    const [p, m] = split(value);
    agents[agent] = { provider: p, model: m, ...keyFor(providers, p) };
  }
  section.agents = agents;
  return section;
}

// "analyst, admin viewer" -> ["analyst", "admin", "viewer"].
export const parseRoles = (text) => String(text || "").split(/[\s,]+/).filter(Boolean);
