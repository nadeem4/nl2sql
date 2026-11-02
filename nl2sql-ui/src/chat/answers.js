// Pure helpers for "Did this answer it?" and My answers (wave_d.md, section 18). The server keeps the plan from its own
// copy of the step; the page sends only which run, which step of which conversation, and an optional note.
import { NOTE_MAX } from "./feedback.js";

// The `ask` step as the server counts it: turns and steps count from 1, and a step is one of the turn's answered asks.
// Null for a step that is not one.
export function stepRef(turn, step) {
  const asks = (turn.steps || []).filter((s) => s.call && s.call.name === "ask" && s.result);
  const at = asks.indexOf(step);
  return at < 0 ? null : { turn: turn.index, step: at + 1 };
}

export function saveBody(result, conversationId, ref, note) {
  const body = { trace_id: result.trace_id, conversation_id: conversationId, step: ref };
  const text = (note || "").trim().slice(0, NOTE_MAX);
  return text ? { ...body, note: text } : body;
}

export const savedLine = (report) =>
  report && report.proposed ? "Saved to My answers and sent for review." : "Saved to My answers.";

export function describeSaveError(e) {
  switch (e && e.code) {
    case "ANSWER_LIMIT_REACHED": return "My answers is full. Delete one on the Dashboards page to save this.";
    case "CONFLICT": return "Another save was in progress, so try again in a moment.";
    default: return (e && e.message) || "That could not be saved.";
  }
}

// A saved answer whose plan the schema has outgrown comes back as an error result led by this code.
export const needsFixing = (result) =>
  Boolean(result && result.errors && result.errors[0] && result.errors[0].error_code === "TILE_NEEDS_FIXING");

// How a re-run is shown: its rows, or the reason there are none.
export function runView(result) {
  if (needsFixing(result)) {
    return { kind: "needs_fixing", message: "This answer needs fixing: the schema has changed since it was saved." };
  }
  if (result.status === "needs_approval") return { kind: "approval", approvalIds: (result.approvals_needed || []).map((a) => a.id) };
  if (result.status === "error" || result.status === "failed") {
    const first = (result.errors || [])[0];
    return { kind: "error", message: (first && first.message) || "The answer could not be run." };
  }
  return { kind: "rows" };
}

export const answerDate = (answer) => String(answer.created_at || "").slice(0, 10);

// The first-time hint is a browser-local flag, never server state. Storage can be missing or throw.
export const HINT_KEY = "nl2sql.ui.answerHintSeen";

export function hintSeen(storage) {
  try {
    return storage.getItem(HINT_KEY) === "1";
  } catch {
    return false;
  }
}

export function markHintSeen(storage) {
  try {
    storage.setItem(HINT_KEY, "1");
  } catch {
    // Not kept: the hint shows again next time.
  }
}
