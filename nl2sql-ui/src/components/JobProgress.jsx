import React from "react";
import { jobState } from "./jobProgress.js";

// A background job (index, profile, eval, dashboard) as a line and a bar. `job` is the API's `Job`; the page that
// started it keeps it current (`followJob` in api/jobs.js) and passes `onCancel` while it can still be cancelled.
export default function JobProgress({ job, onCancel }) {
  const state = jobState(job);
  const share = state.fraction === null ? 0 : state.fraction;
  return (
    <div className="job-progress" role="status">
      <p className="art-meta">{state.label}{state.total ? ` (${state.done} of ${state.total})` : ""}</p>
      {state.fraction !== null && <span className="bar" style={{ "--share": share }}><i /></span>}
      {onCancel && !state.over && <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>}
    </div>
  );
}
