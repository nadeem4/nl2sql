import React, { useEffect, useRef, useState } from "react";
import { listDatasources } from "../api/chat.js";
import { startDashboardJob } from "../api/dashboards.js";
import { cancelJob, followJob, listJobs } from "../api/jobs.js";
import JobProgress from "../components/JobProgress.jsx";
import { describeGenerateError, findRunning, generateFlow, resumeFlow } from "./generate.js";

const api = { startJob: startDashboardJob, follow: followJob };
const running = (job) => job && ["queued", "running"].includes(job.status);

// Builds a starter dashboard for a datasource from what the project already knows; no model is called. The job is
// followed here, and the new dashboard opens (`onOpen(hash)`) when it is saved.
export default function Generate({ workspaces, selected, onOpen }) {
  const [sources, setSources] = useState(null);
  const [datasourceId, setDatasourceId] = useState("");
  const [title, setTitle] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [maxTiles, setMaxTiles] = useState("");
  const [job, setJob] = useState(null);
  const [fault, setFault] = useState(null);
  const [starting, setStarting] = useState(false);
  const abort = useRef(null);

  useEffect(() => {
    listDatasources()
      .then((all) => setSources(all.filter((d) => !String(d.status || "").startsWith("failed"))))
      .catch((e) => { setSources([]); setFault(describeGenerateError(e)); });
    // A job the caller already has (a reload, another tab) comes back with its progress and Cancel.
    const controller = new AbortController();
    abort.current = controller;
    listJobs().then((all) => {
      const found = findRunning(all);
      if (found && !controller.signal.aborted) follow(resumeFlow(api, found, { onJob: setJob, signal: controller.signal }));
    }).catch(() => {});
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const follow = async (promise) => {
    try {
      const out = await promise;
      if (out.hash) onOpen(out.hash);
    } catch (err) {
      if (err.name !== "AbortError") setFault(describeGenerateError(err));
    }
  };

  const chosen = (sources || []).some((d) => d.id === datasourceId) ? datasourceId : (sources && sources[0] ? sources[0].id : "");
  const into = (workspaces || []).some((w) => w.id === workspaceId) ? workspaceId : selected || "";
  const busy = running(job) || starting;

  const submit = (e) => {
    e.preventDefault();
    setFault(null);
    setStarting(true);
    const signal = abort.current.signal;
    return follow(generateFlow(api, { datasourceId: chosen, title, workspaceId: into, maxTiles },
      { onJob: setJob, onStarted: () => setStarting(false), signal }));
  };

  return (
    <details className="dash-gen">
      <summary>Generate a starter dashboard</summary>
      <form onSubmit={submit} className="dash-gen-form">
        <p className="page-note">Builds charts from what the project already knows about the datasource. No model is used.</p>
        {sources && sources.length === 0 && !fault && <p className="quiet">No datasource is ready. Connect one first.</p>}
        <div className="field">
          <label htmlFor="gen-source">Datasource</label>
          <select id="gen-source" value={chosen} disabled={busy || !sources || sources.length === 0} onChange={(e) => setDatasourceId(e.target.value)}>
            {(sources || []).map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="gen-title">Title</label>
          <input id="gen-title" className="dash-input" placeholder="Optional" value={title} disabled={busy} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="gen-workspace">Save in</label>
          <select id="gen-workspace" value={into} disabled={busy || !workspaces || workspaces.length === 0} onChange={(e) => setWorkspaceId(e.target.value)}>
            {(workspaces || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="gen-count">Charts</label>
          <input id="gen-count" className="dash-input dash-short" type="number" min="1" placeholder="Auto" value={maxTiles} disabled={busy} onChange={(e) => setMaxTiles(e.target.value)} />
        </div>
        <button type="submit" className="index-rebuild is-primary" disabled={busy || !chosen}>Generate</button>
        {job && <JobProgress job={job} onCancel={busy ? () => cancelJob(job.id).catch((err) => setFault(describeGenerateError(err))) : undefined} />}
        {fault && <p className="rows-error" role="alert">{fault}</p>}
      </form>
    </details>
  );
}
