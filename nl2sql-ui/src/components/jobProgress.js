// What a JobProgress shows, from a `Job` (nl2sql_api/jobs.py): its status, the latest step and how far along it is.
export function jobState(job) {
  const last = job.progress.length ? job.progress[job.progress.length - 1] : null;
  const done = last ? last.done : 0;
  const total = last ? last.total : null;
  const fraction = job.status === "succeeded" ? 1 : total ? Math.min(done / total, 1) : null;
  const labels = {
    queued: "Waiting to start",
    running: last ? `Running: ${last.step}` : "Running",
    succeeded: "Done",
    failed: `Failed: ${job.error ? job.error.message : "the job stopped with an error."}`,
    cancelled: "Cancelled",
  };
  return {
    status: job.status,
    label: labels[job.status],
    step: last ? last.step : null,
    done,
    total: total ?? null,
    fraction,
    over: !["queued", "running"].includes(job.status),
  };
}
