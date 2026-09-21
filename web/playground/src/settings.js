// Pure helpers for the settings panel. The model list itself lives on the
// server (nl2sql/cli/common/api_key.py) and arrives from GET /api/settings;
// nothing here names a model.

// The options for one node's selector: the default first, then each verified
// model. A model with no temperature runs at its own default, so it says so.
export function modelOptions(models, defaultModel) {
  return [
    { value: "", label: `Default (${defaultModel})` },
    ...(models || []).map((m) => ({
      value: m.id,
      label: m.temperature === null ? `${m.id}, default temperature` : m.id,
    })),
  ];
}

// Node agent -> chosen model id, "" for the default.
export function choicesFrom(nodes) {
  return Object.fromEntries((nodes || []).map((n) => [n.agent, n.model || ""]));
}

// Only the nodes whose choice changed, in the shape POST /api/settings/models
// takes: a model id, or null to go back to the default.
export function changedModels(nodes, choices) {
  const out = {};
  for (const n of nodes || []) {
    const chosen = choices[n.agent] || "";
    if (chosen !== (n.model || "")) out[n.agent] = chosen || null;
  }
  return out;
}

// The chosen models that send no temperature, each named once.
export function variableModels(models, choices) {
  const loose = new Set((models || []).filter((m) => m.temperature === null).map((m) => m.id));
  return [...new Set(Object.values(choices || {}).filter((id) => loose.has(id)))];
}
