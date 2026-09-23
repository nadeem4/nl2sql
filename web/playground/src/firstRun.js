// What the Ask page owes a visitor who has just arrived.
//
// The hosted demo answers on the visitor's own key (`hostedKey.js`), so until
// one is in this tab a question can only come back as the server's 401. The
// page says so before the question box rather than after the click, and holds
// the box and the guided questions closed while it is true.
//
// Local mode is left alone. There a key is already configured, or replay mode
// answers from recordings, and an onboarding wall in front of either would be
// in the way of someone who has nothing to do.

// The sentence attached to every control this state holds closed.
export const NO_KEY_REASON = "Add your API key under Settings to ask a question.";

// True when the Ask page should show the first-run state and hold its controls.
// Only ever true on the hosted demo, and only until a key is in this tab.
export function needsKey(meta, apiKey) {
  return Boolean(meta && meta.hosted) && !(apiKey || "").trim();
}
