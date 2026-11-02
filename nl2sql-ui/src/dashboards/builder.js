import { hashFor } from "../router.js";
import { needsKey } from "../pages/firstRun.js";
import { withStep } from "./generate.js";

// The guided dashboard builder (wave_d.md, section 18): which sources, which chips, which tiles, then the build job.
// Pure helpers so the steps, the bodies and the failure sentences can be tested without a browser.

export const STEPS = ["sources", "inputs", "review", "build"];

export const emptyState = (datasourceId) => ({
  datasourceId, sources: ["curated"], curatedIds: [], savedIds: [], metrics: [], dimensions: [], timeRange: "", text: "",
  tiles: [], title: "", workspaceId: "", timeColumn: "",
});

// A list with `item` added, or removed when it is already there.
export const toggle = (list, item) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);

// Why the model source is closed, or null when it is open. The hosted demo answers a model question only on the
// visitor's own key; anywhere else the server says (MISSING_PROVIDER_KEY) when it has none.
export const NO_KEY_FOR_ASKED = "Needs your API key. Add one under Settings.";
export const askedBlock = (meta, keys) => (needsKey(meta, keys) ? NO_KEY_FOR_ASKED : null);

export function sourceChoices(askedReason) {
  return [
    { id: "curated", label: "Curated", hint: "Questions the project has verified." },
    { id: "saved", label: "My saved answers", hint: "Answers you kept with Yes in chat." },
    { id: "asked", label: "Tell us what matters", hint: "A few new questions, written by the model.",
      disabled: Boolean(askedReason), reason: askedReason || null },
  ];
}

const words = (text) => String(text || "").trim();

// Whether the step has what the next one needs.
export function canAdvance(step, state) {
  const has = (id) => state.sources.includes(id);
  if (step === "sources") return state.sources.length > 0;
  if (step === "inputs") {
    return (has("curated") && state.curatedIds.length > 0) || (has("saved") && state.savedIds.length > 0)
      || (has("asked") && (state.metrics.length > 0 || state.dimensions.length > 0 || words(state.text) !== ""));
  }
  if (step === "review") return state.tiles.some((t) => t.ticked);
  return false;
}

// `POST /dashboard-builder/proposals`: ids and model inputs only for the sources chosen.
export function proposalsBody(state) {
  const has = (id) => state.sources.includes(id);
  const asked = has("asked");
  const body = {
    datasource_id: state.datasourceId, sources: state.sources,
    curated_ids: has("curated") ? state.curatedIds : [], saved_ids: has("saved") ? state.savedIds : [],
    metrics: asked ? state.metrics : [], dimensions: asked ? state.dimensions : [],
  };
  if (asked && state.timeRange) body.time_range = state.timeRange;
  if (asked && words(state.text)) body.text = words(state.text);
  return body;
}

// A model source chosen before the key was known is dropped, not sent.
export const effectiveState = (state, block) => (block ? { ...state, sources: state.sources.filter((s) => s !== "asked") } : state);

// A curated or saved tile runs its own stored query; its question is only a caption, so only a new tile is reworded.
export const canReword = (tile) => tile.source === "asked";

// The chart kinds the API accepts for a tile (ChartChoice).
export const CHARTS = ["auto", "line", "bar", "big_number", "table"];
export const setChart = (tiles, at, chart) => (CHARTS.includes(chart) ? tiles.map((t, i) => (i === at ? { ...t, chart } : t)) : tiles);

export const withTiles = (state, tiles) => ({ ...state, tiles });
export const tick = (tiles, at, ticked) => tiles.map((t, i) => (i === at ? { ...t, ticked } : t));
// A reworded question gets a new title from the server, so the old one is dropped.
export const reword = (tiles, at, question) => tiles.map((t, i) => (i === at ? { ...t, question, title: "" } : t));

// `POST /jobs/dashboard-build`: every tile, with its ticked flag; the server builds the ticked ones.
export function buildBody(state) {
  const body = { datasource_id: state.datasourceId, tiles: state.tiles };
  if (words(state.title)) body.title = words(state.title);
  if (state.workspaceId) body.workspace_id = state.workspaceId;
  if (state.timeColumn) body.time_column = state.timeColumn;
  return body;
}

export const skippedLines = (skipped) => (skipped || []).map((s) => `Couldn't answer "${s.question}": ${s.message}`);

// Only the build job's start can be refused because another job is running (`where` is "build"); any other 409 reads as its own message.
export function describeBuilderError(e, where) {
  if (e && e.code === "MISSING_PROVIDER_KEY") return "Writing new questions needs a model key. Add yours under Settings, or untick \"Tell us what matters\".";
  if (e && e.status === 409 && where === "build") return "You already have a job running. Wait for it to finish or cancel it, then try again.";
  return `${(e && e.message) || String(e)}${e && e.fix ? ` ${e.fix}` : ""}`;
}

// `api` is `{startJob(body), follow(id, onStep, signal)}` as in generate.js. Resolves `{hash, dashboard, report}`,
// `{cancelled: true}` or `{aborted: true}`; a failed job throws its error (`code`, `message`, `fix`).
export async function buildFlow(api, state, { onJob = () => {}, onStarted = () => {}, signal } = {}) {
  let job;
  try {
    job = await api.startJob(buildBody(state));
  } finally {
    onStarted();
  }
  onJob(job);
  if (signal && signal.aborted) return { aborted: true };
  const finished = await api.follow(job.id, (step) => {
    job = withStep(job, step);
    onJob(job);
  }, signal);
  onJob(finished);
  if (signal && signal.aborted) return { aborted: true };
  if (finished.status === "cancelled") return { cancelled: true };
  if (finished.status === "failed") {
    const error = finished.error || {};
    throw Object.assign(new Error(error.message || "The job stopped with an error."), { code: error.code || null, fix: error.fix || null });
  }
  if (finished.status !== "succeeded" || !finished.result) throw new Error("The job is still running. Try again in a moment.");
  const report = finished.result;
  return { hash: hashFor("dashboards", report.dashboard.id), dashboard: report.dashboard, report };
}
