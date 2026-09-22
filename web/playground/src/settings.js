// Pure helpers for the settings panel. The providers and their model lists
// live on the server (nl2sql/cli/common/api_key.py) and arrive from
// GET /api/settings; nothing here names a model or a provider.

// A step's choice as one <select> value: "provider:model", or "" for the default.
export function choiceValue(provider, model) {
  return model ? `${provider}:${model}` : "";
}

// The groups for one step's selector: the default first, then one group per
// provider with its verified models. A provider with no key is shown but
// cannot be chosen. A model with no temperature runs at its own default, so
// it says so.
export function modelGroups(providers, defaultModel) {
  return [
    { label: null, disabled: false, options: [{ value: "", label: `Default (${defaultModel})` }] },
    ...(providers || []).map((p) => ({
      label: p.usable ? p.label : `${p.label} (no key)`,
      disabled: !p.usable,
      options: p.models.map((m) => ({
        value: choiceValue(p.id, m.id),
        label: m.temperature === null ? `${m.id}, default temperature` : m.id,
      })),
    })),
  ];
}

// Step agent -> its current choice value. A step saved before steps had their
// own provider is on the default's provider.
export function choicesFrom(nodes, defaultProvider) {
  return Object.fromEntries(
    (nodes || []).map((n) => [n.agent, choiceValue(n.provider || defaultProvider, n.model)]),
  );
}

// Only the steps whose choice changed, in the shape POST /api/settings/models
// takes: a provider and a model, or null to go back to the default.
export function changedModels(nodes, choices, defaultProvider) {
  const out = {};
  const before = choicesFrom(nodes, defaultProvider);
  for (const n of nodes || []) {
    const chosen = choices[n.agent] || "";
    if (chosen === before[n.agent]) continue;
    if (!chosen) {
      out[n.agent] = null;
    } else {
      const [provider, ...rest] = chosen.split(":");
      out[n.agent] = { provider, model: rest.join(":") };
    }
  }
  return out;
}

// The chosen models that send no temperature, each named once.
export function variableModels(providers, choices) {
  const loose = new Set(
    (providers || []).flatMap((p) => p.models.filter((m) => m.temperature === null).map((m) => choiceValue(p.id, m.id))),
  );
  const chosen = new Set(Object.values(choices || {}).filter((value) => loose.has(value)));
  return [...chosen].map((value) => value.split(":").slice(1).join(":"));
}
