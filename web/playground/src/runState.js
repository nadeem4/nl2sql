// What the Ask page shows for a run, as plain data: which station is live,
// what each station's head says, what the answer header leads with, and what
// the busy button does. No React here, so `npm test` covers it with node's own
// test runner.

import { deniedTables } from "./run.js";

export const STATIONS = ["plan", "checks", "sql", "rows", "cost"];

// The Ask button turns into Stop only once a run has had time to start, so a
// quick double press never cancels the question it just sent.
export const STOP_AFTER_MS = 600;

export const secs = (n) => {
  if (n === undefined || n === null) return "-";
  const s = Number(n);
  if (s >= 1) return `${s.toFixed(2)} s`;
  if (s < 0.001) return "<1 ms";
  return `${Math.round(s * 1000)} ms`;
};

const all = (state) => Object.fromEntries(STATIONS.map((name) => [name, state]));
const checksOf = (sub) => (sub && sub.validation) || [];
const refused = (sub) => checksOf(sub).some((c) => !c.passed);

// Station states: idle (nothing asked), busy (the live one), queued (waiting
// behind it), done, stopped (the gate held the plan), held (plan only: rows
// were not fetched), skipped (never reached).
//
// `/api/ask` reports nothing until the whole run is back, so while it is busy
// the first station is the live one and the rest wait behind it.
export function stationStates({ asked, busy, stopped, result, sub }) {
  if (!asked) return all("idle");
  if (stopped) return all("skipped");
  if (busy) return { ...all("queued"), plan: "busy" };
  if (!result || result.replay_miss || !sub) {
    return { ...all("skipped"), cost: result && !result.replay_miss ? "done" : "skipped" };
  }
  return {
    plan: "done",
    checks: refused(sub) ? "stopped" : "done",
    sql: sub.sql ? "done" : "skipped",
    rows: result.status === "plan_only" ? "held" : sub.rows ? "done" : "skipped",
    cost: "done",
  };
}

// The node each station reports, and whether a model or code decides it.
const NODES = {
  plan: { node: "ast_planner", kind: "model" },
  checks: { node: "logical_validator", kind: "code" },
  sql: { node: "generator", kind: "code" },
  rows: { node: "executor", kind: "code" },
  cost: { node: "LangGraph", kind: "code" },
};

// Each station's head: the kind tag ("Model · gpt-5.4" or "Code") and the
// step's time, right-aligned. Queued and skipped stations say so instead.
export function stationHeads(result, states) {
  const usage = (result && result.usage && result.usage.nodes) || {};
  const timings = (result && result.timings) || {};
  const heads = {};
  for (const name of STATIONS) {
    const { node, kind } = NODES[name];
    const model = usage[node] && usage[node].model;
    const tag = name === "cost" ? null : kind === "model" ? (model ? `Model · ${model}` : "Model") : "Code";
    const state = states[name];
    const time = state === "queued" ? "waiting"
      : state === "skipped" ? "not reached"
        : timings[node] !== undefined && state !== "idle" && state !== "busy" ? secs(timings[node]) : null;
    heads[name] = { kind, tag, time };
  }
  return heads;
}

// What the answer header leads with, once a result is back.
export function answerHead(result, sub) {
  if (!result) return null;
  if (sub && refused(sub)) return { kind: "refused", text: "Refused at the checks. No SQL was written." };
  if (result.status === "plan_only") return { kind: "plan_only", text: "Plan only: the plan passed the checks and nothing was executed." };
  const summary = result.final_answer && result.final_answer.summary;
  if (summary) return { kind: "answer", text: summary };
  const rows = sub && sub.rows;
  if (rows) return { kind: "answer", text: `${count(rows.total_rows, "row")} came back.` };
  return null;
}

const count = (n, noun) => `${Number(n).toLocaleString("en-US")} ${n === 1 ? noun : `${noun}s`}`;

// The mono strip under the answer: each item names the station it summarises,
// so a press can take the reader there.
export function statusStrip(result, sub) {
  if (!result) return [];
  const items = [];
  const rows = sub && sub.rows;
  if (rows) items.push({ label: count(rows.total_rows, "row"), target: "pane-rows", tone: null });
  const checks = checksOf(sub);
  if (checks.length) {
    const ok = checks.filter((c) => c.passed).length;
    items.push({
      label: `${ok} of ${checks.length} checks`, target: "pane-validation", tone: ok === checks.length ? "ok" : "fault",
    });
  }
  if (sub && sub.retry_count) items.push({ label: `${sub.retry_count + 1} plans`, target: "pane-validation", tone: null });
  const wall = result.timings && result.timings.LangGraph;
  if (wall !== undefined && wall !== null) items.push({ label: secs(wall), target: "pane-usage", tone: null });
  const total = result.usage && result.usage.total;
  if (total && total.cost !== null && total.cost !== undefined) {
    items.push({ label: `$${Number(total.cost).toFixed(4)}`, target: "pane-usage", tone: null });
  }
  return items;
}

// "Plan 1 refused → Plan 2 passed", when the checks sent a plan back.
export function gateTimeline(sub) {
  if (!sub || !sub.retry_count) return null;
  const steps = [];
  for (let i = 1; i <= sub.retry_count; i++) steps.push({ label: `Plan ${i} refused`, passed: false });
  const last = sub.retry_count + 1;
  const ok = !refused(sub);
  steps.push({ label: `Plan ${last} ${ok ? "passed" : "refused"}`, passed: ok });
  return steps;
}

// Why the gate did what it did, in one sentence: a role denial (named tables,
// which the page sets in mono), the refusal itself, or what sent a plan back.
export function gateReason(sub, result) {
  const failed = checksOf(sub).filter((c) => !c.passed);
  const errors = (result && result.errors) || [];
  if (failed.length) {
    const denied = deniedTables(errors);
    if (denied.length) return { kind: "denied", tables: denied };
    return { kind: "refused", text: failed[0].message };
  }
  if (sub && sub.retry_count) {
    const rejected = errors.filter((e) => e.error_code !== "SECURITY_VIOLATION");
    const hint = ((result && result.warnings) || []).filter((w) => w.node === "refiner");
    const parts = [];
    parts.push(rejected.length ? `Plan 1 was refused: ${rejected[0].message}` : "Plan 1 was refused.");
    if (hint.length) parts.push(`The hint sent back: ${hint[0].message}`);
    parts.push("No SQL was written until a plan passed.");
    return { kind: "retried", text: parts.join(" ") };
  }
  return null;
}

// The error entry the fault box explains: the first one when nothing came
// back, or any provider refusal, which stops a run wherever it happens.
export function runFault(result, sub) {
  if (!result || result.replay_miss) return null;
  const errors = result.errors || [];
  const provider = errors.find((e) => String(e.error_code || "").startsWith("PROVIDER_"));
  if (provider) return provider;
  if (!sub && errors[0]) return errors[0];
  return null;
}

// What the Ask button does right now.
export function askButton({ busy, elapsedMs }) {
  if (!busy) return { label: "Ask", action: "ask" };
  if (elapsedMs < STOP_AFTER_MS) return { label: "Asking", action: "wait" };
  return { label: "Stop", action: "stop" };
}

// What a stopped run says. Stop aborts the request; the server sees the
// connection close and cancels the run before its next step or model call.
// Hosted, the question was charged when it arrived, so it still counts.
export function stoppedNote({ hosted = false } = {}) {
  const said = "Stopped. The server ends the question after the step it was on.";
  return hosted ? `${said} It still counts toward this session's questions.` : said;
}

// After Ask, on a single-column layout the run can sit below the fold. It is
// brought up only when its top is in the lower part of the screen, and only
// as far as it must be ("nearest"), so the question box does not jump away.
export function runScroll(rect, viewportHeight, { reduce = false } = {}) {
  if (!rect || rect.top <= viewportHeight * 0.6) return null;
  return { behavior: reduce ? "auto" : "smooth", block: "nearest" };
}

// "Running · 3.4 s" on screen; the spoken form changes once a second.
export function elapsedLabel(ms, { spoken = false } = {}) {
  if (spoken) {
    const s = Math.floor(ms / 1000);
    return `Running, ${s} ${s === 1 ? "second" : "seconds"}`;
  }
  return `Running · ${(ms / 1000).toFixed(1)} s`;
}

// The suggestion chips: a few from the selected datasource's pile, and how
// many more sit behind the "N more" chip. Fewer once a run is on the page.
export function chipRow(groups, selected, { ran = false } = {}) {
  const list = groups || [];
  if (!list.length) return { datasource: null, chips: [], more: 0 };
  const group = list.find((g) => g.datasource === selected) || list[0];
  const chips = group.questions.slice(0, ran ? 3 : 4);
  const total = list.reduce((n, g) => n + g.questions.length, 0);
  return { datasource: group.datasource, chips, more: total - chips.length };
}
