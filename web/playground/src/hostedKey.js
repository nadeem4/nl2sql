// The visitor's own API key, on the hosted demo. It lives in this tab's
// `sessionStorage` and nowhere else: closing the tab drops it, and the server
// never receives it except as one header on one question.
//
// `sessionStorage` rather than `localStorage` so the promise the page makes --
// "cleared when you close the tab" -- is the browser's own behaviour rather
// than something this code has to remember to do. Storage can be missing or
// throw (private windows, blocked site data), so every access is guarded and
// the key simply lives in memory for that visit instead.

export const KEY_HEADER = "X-NL2SQL-Api-Key";
export const KEY_STORE = "nl2sql.playground.apiKey";

// The same shape the server checks (nl2sql/llm/providers.py): 20 or more
// letters, digits, '-' or '_'. Checked here only to say so before a round
// trip; the server's answer is the one that counts.
const SHAPE = /^[A-Za-z0-9_-]{20,}$/;

export function looksLikeKey(key) {
  return SHAPE.test((key || "").trim());
}

// "sk-proj-....abcd", the same form the server would show. A key too short to
// mask usefully is never shown at all.
export function maskKey(key) {
  const k = (key || "").trim();
  if (k.length < 12) return "***";
  return `${k.slice(0, 3)}...${k.slice(-4)}`;
}

export function readKey(storage) {
  try {
    return (storage && storage.getItem(KEY_STORE)) || "";
  } catch {
    return "";
  }
}

export function writeKey(storage, key) {
  const k = (key || "").trim();
  try {
    if (k) storage.setItem(KEY_STORE, k);
    else storage.removeItem(KEY_STORE);
  } catch {
    /* not kept for this visit; the key still works until the page reloads */
  }
  return k;
}

// The headers one question is sent with. No key means no header: the server
// answers 401 with the sentence that tells the visitor where to add one.
export function askHeaders(key) {
  const headers = { "Content-Type": "application/json" };
  const k = (key || "").trim();
  if (k) headers[KEY_HEADER] = k;
  return headers;
}
