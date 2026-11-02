import { hashFor } from "../router.js";

// Generate a starter dashboard for a datasource: `POST /jobs/dashboard`, followed to its end. It builds from what the
// project already knows and calls no model. Kept out of the component so it can be tested with a fake fetch.

// The request body: only the fields that were set. A chart count under one (or not a number) is left out.
export function generateBody({ datasourceId, title, workspaceId, maxTiles }) {
  const body = { datasource_id: datasourceId };
  if (title && title.trim()) body.title = title.trim();
  if (workspaceId) body.workspace_id = workspaceId;
  const n = Number(maxTiles);
  if (Number.isInteger(n) && n >= 1) body.max_tiles = n;
  return body;
}

// The job with one more progress step ("tile 3 of 8"), running.
export function withStep(job, step) {
  return job ? { ...job, status: "running", progress: [...job.progress, step] } : job;
}

// The dashboard job the caller already has queued or running, if any (from `GET /jobs`, newest first).
export function findRunning(list) {
  return (list || []).find((j) => j.kind === "dashboard" && ["queued", "running"].includes(j.status)) || null;
}

// `api` is `{startJob(body), follow(id, onStep, signal)}` (api/dashboards.js `startDashboardJob`, api/jobs.js `followJob`).
// `onStarted()` fires once the start request has returned or failed, `onJob(job)` hears the queued job, each step and
// the finished job. `signal` stops it: an aborted flow never opens a dashboard. Resolves `{hash, dashboard}` to open,
// `{cancelled: true}` or `{aborted: true}`; a failed job throws its error (`message`, `fix`).
export async function generateFlow(api, input, { onJob = () => {}, onStarted = () => {}, signal } = {}) {
  let job;
  try {
    job = await api.startJob(generateBody(input));
  } finally {
    onStarted();
  }
  return resumeFlow(api, job, { onJob, signal });
}

// Follow a job that is already started (a new one, or one found running on load) to its end.
export async function resumeFlow(api, job, { onJob = () => {}, signal } = {}) {
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
    throw Object.assign(new Error(error.message || "The job stopped with an error."), { fix: error.fix || null });
  }
  if (finished.status !== "succeeded" || !finished.result) throw new Error("The job is still running. Try again in a moment.");
  const { dashboard } = finished.result;
  return { hash: hashFor("dashboards", dashboard.id), dashboard };
}

// One sentence for a failure of the start call.
export function describeGenerateError(e) {
  if (e && e.status === 409) return "You already have a job running. Wait for it to finish or cancel it, then try again.";
  return `${e.message || String(e)}${e.fix ? ` ${e.fix}` : ""}`;
}
