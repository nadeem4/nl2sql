// What the Ask page owes a visitor who has just arrived.
//
// The hosted demo answers live on the visitor's own key (`hostedKey.js`). A
// visitor without one is not locked out: the guided questions the server has
// recordings for (`meta.recorded`) replay a real earlier run, labelled as one,
// and any other question comes back as a replay miss that asks for a key. So
// the page says what is on offer above the question box and keeps every
// control open.
//
// Local mode is left alone. There a key is already configured, or replay mode
// answers from recordings, and an onboarding block in front of either would be
// in the way of someone who has nothing to do.

// The sentence the status pill carries while there is no key.
export const NO_KEY_REASON = "Add your API key under Settings to ask a question of your own.";

function held(apiKey) {
  return (apiKey && typeof apiKey === "object" ? Object.values(apiKey) : [apiKey])
    .filter((key) => (key || "").trim());
}

// True when the Ask page should show the first-run block: on the hosted demo,
// until a key is in this tab. Any one key ends it, since any one can answer.
export function needsKey(meta, apiKey) {
  return Boolean(meta && meta.hosted) && !held(apiKey).length;
}

// Whether the question box, Ask and the guided questions are held closed.
// Never: a keyless visitor can replay the guided questions, and a question
// without a recording answers with the way to add a key. Kept as a function so
// the rule is stated, and tested, in one place.
export function lockControls() {
  return false;
}

// Whether `question` has a recorded answer a keyless visitor is served.
export function isRecorded(meta, question) {
  const recorded = (meta && meta.recorded) || [];
  return recorded.includes((question || "").trim());
}

// The first-run block's words. With recordings the guided questions lead and
// the key form waits, folded; without them the key is the only way in.
export function firstRunCopy(meta) {
  const recorded = ((meta && meta.recorded) || []).length;
  if (recorded) {
    return {
      why: "Pick a guided question below to see a recorded run: the plan, the checks, the SQL and the rows from an "
        + "earlier live run, replayed without a key. To ask your own question, add your API key.",
      keyOpen: false,
    };
  }
  return {
    why: "This demo runs on your own API key. The model writes a typed plan, the checks review it, and only then is "
      + "SQL written and run. The sample databases and their search index are already built, so the key is the only "
      + "thing missing.",
    keyOpen: true,
  };
}

// What a replay miss says. Hosted, it points at Settings, where the key goes;
// in local replay mode there is no tab-held key, so it only says what happened.
export function missPrompt(result, meta) {
  if (!result || !result.replay_miss) return null;
  if (meta && meta.hosted) {
    return {
      text: "This question has no recorded answer. Add your own API key to ask it live; it stays in this browser tab.",
      action: { route: "#/settings", label: "Add a key in Settings" },
    };
  }
  return { text: "No recorded answer for this question. Add an API key to ask it live.", action: null };
}

// The label a replayed answer carries, so nobody mistakes it for a live run.
export function recordedBadge(result) {
  if (!result || !result.recorded) return null;
  return {
    label: "Recorded run",
    title: "Replayed from a recording of an earlier live run; not run just now. Token counts are placeholders.",
  };
}

// What the top bar's mode line says on the hosted demo: whose key answers and
// what the limits are.
//
// Empty until there is a key. The first-run block above the question box is
// already making that case in full, and the Settings form makes it again on
// its own page; a third copy in the top bar is noise. Once a key is in this
// tab the line has something of its own to say, so it comes back.
export function hostedNote(meta, apiKey) {
  const keys = held(apiKey);
  if (!meta || !keys.length) return "";
  const limits = meta.limits || {};
  const capped = limits.questions_per_minute
    ? ` Up to ${limits.questions_per_minute} questions a minute and ${limits.questions_per_session} a session.`
    : "";
  const kept = keys.length === 1 ? "the key" : `the ${keys.length} keys`;
  return `Questions run on ${kept} in this browser tab; ${keys.length === 1 ? "it is" : "each is"} sent with each question and stored nowhere.${capped} Replace or clear ${keys.length === 1 ? "it" : "them"}, or change the model each step uses, under`;
}
