import React, { useEffect, useRef, useState } from "react";
import { listSuites, startEval } from "../api/evals.js";
import { cancelJob, followJob } from "../api/jobs.js";
import JobProgress from "../components/JobProgress.jsx";
import { money, percent, reportView } from "../evals/report.js";
import { isAdmin } from "../lib/caller.js";

// Evals: run the configured suites on the server and read the report. Every call is an admin call (a report holds
// every role's cases, plans and SQL), so a caller who is not an admin is told so instead of being shown a form that
// answers 403. A run that calls the model needs a cost cap; the other modes call no model.
const message = (err) => (err && err.message) || "Something went wrong.";

const MODES = [
  ["plans", "Plans only", "Plans each case and checks the plan. Calls no model for answers."],
  ["retrieval", "Retrieval only", "Checks which tables and columns are found. Calls no model."],
  ["full", "Full", "Asks every case end to end. Calls the model and spends money."],
];

function Off({ reason }) {
  return (
    <div className="settings-off" id="evals-off">
      <h3>Evals are off here</h3>
      <p>{reason}</p>
    </div>
  );
}

function Board({ board }) {
  return (
    <section className="conn-card ev-board" aria-label={`Results for ${board.name}`}>
      <h3>{board.name}</h3>
      <dl className="ev-figures">
        <div><dt>Accuracy</dt><dd>{percent(board.accuracy)}</dd></div>
        {board.generated !== null && <div><dt>Generated path</dt><dd>{percent(board.generated)}</dd></div>}
        <div><dt>Cost</dt><dd>{money(board.cost)}</dd></div>
        {board.p50 !== null && <div><dt>Latency p50 / p95</dt><dd>{board.p50}s / {board.p95}s</dd></div>}
        {board.retries > 0 && <div><dt>Planner retries</dt><dd>{board.retries}</dd></div>}
      </dl>
      {board.interval && (
        <p className="art-meta">95% interval {percent(board.interval[0])} to {percent(board.interval[1])}</p>
      )}
      {board.bySource.length > 0 && (
        <table className="ev-table">
          <caption>By source</caption>
          <thead><tr><th scope="col">Source</th><th scope="col">Passed</th><th scope="col">Accuracy</th></tr></thead>
          <tbody>
            {board.bySource.map((s) => (
              <tr key={s.name}><th scope="row">{s.name}</th><td>{s.pass} of {s.total}</td><td>{percent(s.accuracy)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {board.byRole.length > 0 && (
        <table className="ev-table">
          <caption>By role</caption>
          <thead>
            <tr><th scope="col">Role</th><th scope="col">Pass</th><th scope="col">Fail</th><th scope="col">Error</th><th scope="col">Skip</th></tr>
          </thead>
          <tbody>
            {board.byRole.map((r) => (
              <tr key={r.role}><th scope="row">{r.role}</th><td>{r.pass}</td><td>{r.fail}</td><td>{r.error}</td><td>{r.skip}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {board.failing.length > 0 ? (
        <div>
          <h4>{board.failing.length === 1 ? "1 case did not pass" : `${board.failing.length} cases did not pass`}</h4>
          <ul className="ev-failing">
            {board.failing.map((f) => (
              <li key={`${f.suite}/${f.id}/${f.role}`}>
                <p><span className="mono">{f.suite}/{f.id}</span> <span className="art-meta">{f.role}, {f.status}</span></p>
                <p>{f.question}</p>
                {f.messages.map((m) => <p className="conn-error" key={m}>{m}</p>)}
                {f.sql && <details><summary className="art-meta">SQL</summary><pre className="mono ev-sql">{f.sql}</pre></details>}
              </li>
            ))}
          </ul>
        </div>
      ) : <p className="quiet">Every case passed.</p>}
    </section>
  );
}

function Report({ report }) {
  const view = reportView(report);
  return (
    <div className="ev-report" id="evals-report">
      <p className="art-meta">
        {view.mode} run of {view.suites.join(", ") || "no suites"}; spent {money(view.spent)}
        {view.cap !== null ? ` of ${money(view.cap)}` : ""}
        {view.skipped ? `; ${view.skipped} skipped` : ""}
      </p>
      {view.stopped && <p className="conn-error" role="status">{view.stopped}</p>}
      {view.unknownCost > 0 && (
        <p className="quiet">{view.unknownCost} model {view.unknownCost === 1 ? "call has" : "calls have"} no known price, so the cost is a floor.</p>
      )}
      {view.boards.length === 0 && <p className="quiet">This run scored no cases.</p>}
      {view.boards.map((b) => <Board board={b} key={b.name} />)}
    </div>
  );
}

export default function Evals({ caller = null }) {
  const admin = isAdmin(caller);
  const [suites, setSuites] = useState(null);
  const [picked, setPicked] = useState({});
  const [mode, setMode] = useState("plans");
  const [cap, setCap] = useState("1");
  const [job, setJob] = useState(null);
  const [fault, setFault] = useState(null);
  const abort = useRef(null);

  useEffect(() => () => abort.current && abort.current.abort(), []);
  useEffect(() => {
    if (!admin) return;
    listSuites()
      .then((list) => { setSuites(list); setPicked(Object.fromEntries(list.map((s) => [s.name, true]))); })
      .catch((err) => setFault(message(err)));
  }, [admin]);

  if (!admin) return <Off reason="Running evals needs an administrator: a report holds every role's cases, plans and SQL." />;

  const capValue = Number(cap);
  const needsCap = mode === "full";
  const capOk = !needsCap || (Number.isFinite(capValue) && capValue > 0);
  const chosen = (suites || []).filter((s) => picked[s.name]).map((s) => s.name);
  const running = job && ["queued", "running"].includes(job.status);

  const run = async () => {
    setFault(null);
    setJob(null);
    abort.current = new AbortController();
    try {
      let current = await startEval({
        mode, suites: chosen.length === suites.length ? [] : chosen, maxCostUsd: needsCap ? capValue : undefined,
      });
      setJob(current);
      current = await followJob(current.id, (step) =>
        setJob((j) => (j ? { ...j, status: "running", progress: [...j.progress, step] } : j)), abort.current.signal);
      setJob(current);
    } catch (err) {
      if (err.name !== "AbortError") setFault(message(err));
    }
  };

  if (suites && !suites.length) {
    return <p className="quiet" id="evals-none">No eval suites are configured. Add paths under <code>evals.suites</code> in the project file.</p>;
  }

  return (
    <div className="conn" id="evals">
      <section className="conn-card" aria-labelledby="evals-run-heading">
        <h3 id="evals-run-heading">Run</h3>
        <fieldset className="conn-group">
          <legend>Mode</legend>
          {MODES.map(([value, label, hint]) => (
            <label className="check" key={value}>
              <input type="radio" name="evals-mode" checked={mode === value} onChange={() => setMode(value)} />
              <span>{label}<span className="art-meta"> {hint}</span></span>
            </label>
          ))}
        </fieldset>
        <fieldset className="conn-group">
          <legend>Suites</legend>
          {(suites || []).map((s) => (
            <label className="check" key={s.name}>
              <input type="checkbox" checked={Boolean(picked[s.name])}
                     onChange={(e) => setPicked({ ...picked, [s.name]: e.target.checked })} />
              <span><span className="mono">{s.name}</span><span className="art-meta"> {s.approved} of {s.cases} cases approved</span></span>
            </label>
          ))}
        </fieldset>
        {needsCap && (
          <div className="conn-field">
            <label htmlFor="evals-cap">Cost cap in US dollars</label>
            <input id="evals-cap" inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)}
                   aria-invalid={capOk ? undefined : true} />
            <p className="conn-help">The run stops when its model calls reach this amount.</p>
          </div>
        )}
        <div className="conn-actions">
          <button type="button" className="index-rebuild" id="evals-run" onClick={run}
                  disabled={running || !suites || !chosen.length || !capOk}>Run</button>
        </div>
        {!capOk && <p className="conn-error">Enter an amount above zero.</p>}
        {job && <JobProgress job={job} onCancel={running ? () => cancelJob(job.id) : undefined} />}
        {fault && <p className="fault" role="alert">{fault}</p>}
      </section>
      {job && !running && job.result && <Report report={job.result} />}
    </div>
  );
}
