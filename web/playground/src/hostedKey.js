// The visitor's own API keys, on the hosted demo: one per provider. They live
// in this tab's `sessionStorage` and nowhere else: closing the tab drops them,
// and the server never receives one except as one header on one question.
//
// `sessionStorage` rather than `localStorage` so the promise the page makes --
// "cleared when you close the tab" -- is the browser's own behaviour rather
// than something this code has to remember to do. Storage can be missing or
// throw (private windows, blocked site data), so every access is guarded and
// the keys simply live in memory for that visit instead.
//
// One header per provider (`X-NL2SQL-Api-Key-anthropic`), so a key never
// shares a header with anything else: nothing that parses, quotes or logs a
// header can put two keys, or a key and something else, in one place. The
// per-step model choices travel separately, in `X-NL2SQL-Models`, which
// carries no secret at all (see `hostedModels.js`).

export const KEY_HEADER = "X-NL2SQL-Api-Key";
export const MODELS_HEADER = "X-NL2SQL-Models";
// Where one provider's key is kept. The older single-key store is read once
// and moved here, so a tab open across the change keeps its key.
export const KEY_STORE = "nl2sql.playground.apiKey";
export const keyStoreFor = (provider) => `${KEY_STORE}.${provider}`;

// The providers a key can be kept for, in the order the page lists them. The
// server's own list is PROVIDER_PRESETS; a provider it does not know would
// simply never be asked for.
export const KEY_PROVIDERS = ["openai", "anthropic", "openrouter"];

// The same shape the server checks (nl2sql/llm/providers.py): 20 or more
// letters, digits, '-' or '_'. Checked here only to say so before a round
// trip; the server's answer is the one that counts.
const SHAPE = /^[A-Za-z0-9_-]{20,}$/;

export function looksLikeKey(key) {
  return SHAPE.test((key || "").trim());
}

// Which provider a key belongs to, by its own documented prefix, exactly as
// the server reads it (`provider_for_key`).
export function providerForKey(key) {
  const k = (key || "").trim();
  if (k.startsWith("sk-ant-")) return "anthropic";
  if (k.startsWith("sk-or-")) return "openrouter";
  return "openai";
}

// "sk-proj-....abcd", the same form the server would show. A key too short to
// mask usefully is never shown at all.
export function maskKey(key) {
  const k = (key || "").trim();
  if (k.length < 12) return "***";
  return `${k.slice(0, 3)}...${k.slice(-4)}`;
}

function get(storage, name) {
  try {
    return (storage && storage.getItem(name)) || "";
  } catch {
    return "";
  }
}

function put(storage, name, value) {
  try {
    if (value) storage.setItem(name, value);
    else storage.removeItem(name);
  } catch {
    /* not kept for this visit; the key still works until the page reloads */
  }
}

// Every key this tab holds, as {provider: key}. A key left by the older
// single-key form is moved to its provider's slot on the way past.
export function readKeys(storage) {
  const keys = {};
  for (const provider of KEY_PROVIDERS) {
    const key = get(storage, keyStoreFor(provider)).trim();
    if (key) keys[provider] = key;
  }
  const legacy = get(storage, KEY_STORE).trim();
  if (legacy) {
    const provider = providerForKey(legacy);
    if (!keys[provider]) {
      keys[provider] = legacy;
      put(storage, keyStoreFor(provider), legacy);
    }
    put(storage, KEY_STORE, "");
  }
  return keys;
}

// Saves or clears one provider's key and returns the whole set again, so the
// page has one value to hold rather than a copy per provider.
export function writeKeyFor(storage, provider, key) {
  const k = (key || "").trim();
  put(storage, keyStoreFor(provider), k);
  const keys = readKeys(storage);
  // Storage can be missing or refuse to keep anything; the key still has to
  // work for this visit, so the answer is what was asked for either way.
  if (k) keys[provider] = k;
  else delete keys[provider];
  return keys;
}

// The headers one question is sent with: one per provider whose key this tab
// holds, and the step choices when any were made. No key means no key header:
// the server answers 401 with the sentence that tells the visitor where to add
// one.
export function askHeaders(keys, models) {
  const headers = { "Content-Type": "application/json" };
  const held = typeof keys === "string" || keys === null || keys === undefined
    // One key and no provider named: what the page used to send.
    ? (keys ? { [providerForKey(keys)]: String(keys).trim() } : {})
    : keys;
  const names = Object.keys(held || {}).filter((p) => (held[p] || "").trim());
  for (const provider of names) {
    headers[`${KEY_HEADER}-${provider}`] = held[provider].trim();
  }
  // With exactly one key, the unnamed header keeps the simplest path working
  // exactly as it did: one key, defaults everywhere, ask a question.
  if (names.length === 1) headers[KEY_HEADER] = held[names[0]].trim();
  const chosen = models && Object.keys(models).length ? models : null;
  if (chosen) headers[MODELS_HEADER] = JSON.stringify(chosen);
  return headers;
}
