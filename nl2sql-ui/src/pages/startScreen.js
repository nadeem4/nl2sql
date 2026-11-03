// The start screen (wave_d.md, section 4): what `GET /meta`'s `start` asks the page to show, and the one action that
// calls a route. The page computes nothing: `start` says which screen applies.
//
// - "choose": the served project has no datasource yet. Try the demo (`POST /jobs/demo`, admin) and Connect your
//   database (the Connect page).
// - "hosted": the hosted demo. Try the demo opens Chat; Connect your database connects nothing on the hosted server,
//   it shows how to run nl2sql yourself and calls no route.
// - anything else: no start screen.

import { mayAdminister } from "../lib/caller.js";
import { withStep } from "../dashboards/generate.js";
import { DOCS } from "./home.js";

export const SELF_HOST_GUIDE = `${DOCS}deployment/compose/`;

// The run-it-yourself steps the hosted "Connect" shows: install and launch, then Compose, then the guide.
export const HOSTED_STEPS = [
  { label: "Install it and start the demo on your machine", code: ['pip install "nl2sql-engine[demo]"', "nl2sql demo"] },
  { label: "Or self-host it with Docker Compose", code: ["docker compose up -d --build"] },
  { label: "The self-hosting guide has the rest", href: SELF_HOST_GUIDE },
];

// `{kind: "choose", canAct}`, `{kind: "hosted"}` or null. `canAct`: whether to offer the two actions, which are
// admin work; the route's 403 stays the final word when the caller is not known.
export function startView(meta, caller) {
  if (!meta) return null;
  if (meta.start === "hosted") return { kind: "hosted" };
  if (meta.start === "choose") return { kind: "choose", canAct: mayAdminister(caller) };
  return null;
}

// Try the demo on an empty project: start the job and follow it to its end. `api` is `{startDemo(), follow(id,
// onStep, signal)}` (api/meta.js, api/jobs.js). `onJob(job)` hears the queued job, each step and the finished one.
// Resolves `{done: true}` or `{cancelled: true}`; a failed job throws its message and fix.
export async function demoFlow(api, { onJob = () => {}, signal } = {}) {
  let job = await api.startDemo();
  onJob(job);
  const finished = await api.follow(job.id, (step) => {
    job = withStep(job, step);
    onJob(job);
  }, signal);
  onJob(finished);
  if (finished.status === "cancelled") return { cancelled: true };
  if (finished.status === "failed") {
    const error = finished.error || {};
    throw Object.assign(new Error(error.message || "The job stopped with an error."), { fix: error.fix || null });
  }
  return { done: true };
}
