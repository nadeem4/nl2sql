// The model each step runs on, on the hosted demo.
//
// Choosing a model asks the server to remember nothing, so the hosted demo
// keeps the choice the same way it keeps a key: in this tab's
// `sessionStorage`, sent with each question in one compact header
// (`X-NL2SQL-Models`, built by `askHeaders`). It holds no secret -- a step
// name, a provider and a model -- so unlike a key it is safe to parse and safe
// for the server to quote back in a refusal.
//
// The stored shape is the same one the header carries: agent name ->
// "provider:model". A step with no entry runs on the default, which is the
// whole of the simple path: one key, defaults everywhere, ask a question.

export const MODELS_STORE = "nl2sql.playground.stepModels";

// "provider:model" is a legal choice; anything else is dropped rather than
// sent to the server, which would only refuse it.
const CHOICE = /^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]+$/;

export function readModels(storage) {
  let raw = "";
  try {
    raw = (storage && storage.getItem(MODELS_STORE)) || "";
  } catch {
    return {};
  }
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => typeof value === "string" && CHOICE.test(value)),
    );
  } catch {
    return {};
  }
}

// Sets or clears one step's choice and returns the whole set again. An empty
// choice is the default, which is stored as nothing at all.
export function writeModel(storage, models, agent, choice) {
  const next = { ...(models || {}) };
  if (choice && CHOICE.test(choice)) next[agent] = choice;
  else delete next[agent];
  try {
    if (Object.keys(next).length) storage.setItem(MODELS_STORE, JSON.stringify(next));
    else storage.removeItem(MODELS_STORE);
  } catch {
    /* not kept for this visit; the choice still applies until the page reloads */
  }
  return next;
}

// The providers the chosen steps need a key for, each named once, so the page
// can say which key is still missing before the server refuses the question.
export function providersNeeded(models) {
  const providers = [];
  for (const value of Object.values(models || {})) {
    const provider = String(value).split(":")[0];
    if (provider && !providers.includes(provider)) providers.push(provider);
  }
  return providers;
}

// What the collapsed section says it will do: how many steps are on something
// other than the default.
export function chosenCount(models) {
  return Object.keys(models || {}).length;
}
