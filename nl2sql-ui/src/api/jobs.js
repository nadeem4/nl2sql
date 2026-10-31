// A background job, whatever started it (index, profile, eval, dashboard): the calls every page that shows a
// JobProgress shares. The call that starts a job belongs to the page for its kind.
import { request, stream } from "./http.js";

export const getJob = (id) => request("GET", `/jobs/${encodeURIComponent(id)}`);

export const cancelJob = (id) => request("DELETE", `/jobs/${encodeURIComponent(id)}`);

// Follows a job to its end: `onProgress(step)` for each `job.progress`, then resolves with the finished `Job`
// (`job.finished`). A job that is already over sends its history and the end at once.
export async function followJob(id, onProgress, signal) {
  let finished = null;
  await stream(`/jobs/${encodeURIComponent(id)}/events`, undefined, ({ event, data }) => { // GET
    if (event === "job.progress") onProgress(data);
    else if (event === "job.finished") finished = data;
  }, signal);
  return finished;
}
