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
//
// A tab can hold a key per provider (`hostedKey.js`), so this takes either the
// whole set or one key on its own; any one of them ends the first-run state,
// because any one of them can answer a question.
export function needsKey(meta, apiKey) {
  const keys = apiKey && typeof apiKey === "object" ? Object.values(apiKey) : [apiKey];
  return Boolean(meta && meta.hosted) && !keys.some((key) => (key || "").trim());
}

// What the top bar's mode line says on the hosted demo: whose key answers and
// what the limits are.
//
// Empty until there is a key. The first-run state above the question box is
// already making that case in full, and the Settings form makes it again on
// its own page; a third copy in the top bar is noise, and it pulled the eye
// away from the one place that can do something about it. Once a key is in
// this tab the line has something of its own to say, so it comes back.
export function hostedNote(meta, apiKey) {
  const held = (apiKey && typeof apiKey === "object" ? Object.values(apiKey) : [apiKey])
    .filter((key) => (key || "").trim());
  if (!meta || !held.length) return "";
  const limits = meta.limits || {};
  const capped = limits.questions_per_minute
    ? ` Up to ${limits.questions_per_minute} questions a minute and ${limits.questions_per_session} a session.`
    : "";
  const kept = held.length === 1 ? "the key" : `the ${held.length} keys`;
  return `Questions run on ${kept} in this browser tab; ${held.length === 1 ? "it is" : "each is"} sent with each question and stored nowhere.${capped} Replace or clear ${held.length === 1 ? "it" : "them"}, or change the model each step uses, under`;
}
