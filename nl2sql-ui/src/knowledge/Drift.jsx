import React, { useState } from "react";
import { checkDrift } from "../api/knowledge.js";
import { driftSummary, sortReports } from "./drift.js";

// Has the live database moved since the index was built? One check across the datasources, then each report in words:
// what changed and what the change would break (verified queries, glossary items, policy rules, preferred tables).
export default function Drift({ datasourceIds }) {
  const [reports, setReports] = useState(null);
  const [fault, setFault] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setFault(null);
    try { setReports(sortReports(await checkDrift(datasourceIds))); }
    catch (err) { setFault(err.message || "Something went wrong."); }
    finally { setBusy(false); }
  };
  return (
    <div className="kn-drift">
      <button type="button" className="index-rebuild" disabled={busy} onClick={run}>Check for drift</button>
      {fault && <p className="fault" role="alert">{fault}</p>}
      {reports && reports.map((r) => {
        const s = driftSummary(r);
        return (
          <article className="kn-report" key={r.datasource_id} data-tone={s.tone}>
            <h4><span className="mono">{r.datasource_id}</span> <span className="kn-tone">{s.label}</span></h4>
            {s.error && <p className="fault">{s.error}</p>}
            {s.changes.length > 0 && <ul className="kn-list">{s.changes.map((c) => <li key={c}>{c}</li>)}</ul>}
            {s.impact.map((i) => (
              <div key={i.label}>
                <p className="art-meta">May no longer hold: {i.label.toLowerCase()}</p>
                <ul className="kn-list">{i.lines.map((l) => <li key={l}>{l}</li>)}</ul>
              </div>
            ))}
            {r.status === "drifted" && <p className="quiet">Rebuild the index from Connect to bring it up to date.</p>}
          </article>
        );
      })}
    </div>
  );
}
