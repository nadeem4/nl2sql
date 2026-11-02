// What the transcript reads off a stored `Turn` (nl2sql/conversations/models.py). Plain data in, plain data out.

const LABELS = { chart: "Chart", diagram: "Diagram", report: "Report", dashboard_pin: "Pin to dashboard", dashboard_edit: "Edit dashboard" };

export function toolLabel(name) {
  if (LABELS[name]) return LABELS[name];
  const words = String(name || "").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const answerOf = (turn) => turn.reply || "";

export const askSteps = (turn) => (turn.steps || []).filter((s) => s.call && s.call.name === "ask");

export function pendingClarification(turn) {
  if (turn.status !== "needs_clarification") return null;
  for (const s of askSteps(turn)) if (s.result && s.result.clarification) return s.result.clarification;
  return null;
}

export function pendingApprovals(turn) {
  if (turn.status !== "needs_approval") return [];
  return askSteps(turn).flatMap((s) => (s.result && s.result.approvals_needed) || []);
}

export const outputsOf = (turn) =>
  (turn.steps || []).flatMap((s, index) => (s.output ? [{ index, output: s.output }] : []));

export const offersFor = (turn) => turn.offers || [];

// The tools worth a button on an answered turn: those the server lists. Pinning needs a dashboard to pin to.
const BUTTON_ORDER = ["chart", "diagram", "report", "dashboard_pin"];

export function toolButtons(turn, conversation, tools) {
  if (turn.status !== "answered" || !askSteps(turn).length) return [];
  const listed = new Set((tools || []).map((t) => t.name));
  return BUTTON_ORDER.filter((name) => listed.has(name) && (name !== "dashboard_pin" || conversation.dashboard_id))
    .map((name) => ({ name, label: toolLabel(name) }));
}

export function statusLine(turn) {
  switch (turn.status) {
    case "needs_clarification": return "The model needs you to pick the meaning of the question.";
    case "needs_approval": return "This answer is waiting for an approval.";
    case "stopped": return "The turn stopped before it could answer.";
    default: return null;
  }
}

export const errorLine = (turn) => (turn.status === "error" ? (turn.error && turn.error.message) || "The turn failed." : null);

// The arguments a tool button sends: turns and steps count from 1, and a step is one of the turn's answers.
export function toolArgs(name, turn) {
  if (name === "chart") return { turn: turn.index, step: 1 };
  if (name === "diagram") return { view: "answer", turn: turn.index, step: 1 };
  return {};
}
