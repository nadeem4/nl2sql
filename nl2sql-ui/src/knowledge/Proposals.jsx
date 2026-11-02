import React, { useState } from "react";
import { acceptProposals, rejectProposals } from "../api/knowledge.js";
import {
  acceptBody, acceptOutcome, changeLines, editableFields, evidenceLines, groupByKind, rejectBody, summaryOf,
} from "./proposals.js";

// The review list: proposals by kind, each with its evidence. Accept (optionally with edited text) and Reject act on
// the ticked ones, or on one row. Counts and checks are shown, never rows of the data.
export default function Proposals({ proposals, onChanged }) {
  const [ticked, setTicked] = useState({});
  const [drafts, setDrafts] = useState({});
  const [open, setOpen] = useState(null);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState(null);
  const [fault, setFault] = useState(null);
  const [busy, setBusy] = useState(false);
  const byId = Object.fromEntries(proposals.map((p) => [p.id, p]));

  const run = async (ids, call, body) => {
    setBusy(true); setFault(null); setNote(null);
    try {
      const report = await call(body);
      if (report && report.accepted) setNote(acceptOutcome(report));
      else setNote({ summary: `Rejected ${ids.length}.`, refused: [], warnings: [] });
      setTicked({}); setOpen(null); setReason("");
      onChanged();
    } catch (err) {
      setFault(err.message || "Something went wrong.");
    } finally { setBusy(false); }
  };
  const chosen = Object.keys(ticked).filter((id) => ticked[id]);
  const accept = (ids) => run(ids, acceptProposals, acceptBody(ids, drafts, byId));
  const reject = (ids) => run(ids, rejectProposals, rejectBody(ids, reason));

  if (!proposals.length && !note) return <p className="quiet">Nothing is waiting for review.</p>;
  return (
    <div className="kn-proposals">
      {chosen.length > 0 && (
        <div className="conn-actions" role="group" aria-label="Selected proposals">
          <span className="art-meta">{chosen.length} selected</span>
          <button type="button" className="settings-save" disabled={busy} onClick={() => accept(chosen)}>Accept selected</button>
          <button type="button" className="index-rebuild" disabled={busy} onClick={() => reject(chosen)}>Reject selected</button>
        </div>
      )}
      {groupByKind(proposals).map((g) => (
        <section key={g.kind} aria-label={g.label}>
          <h4>{g.label} <span className="art-meta">{g.items.length}</span></h4>
          <ul className="kn-prop-list">
            {g.items.map((p) => (
              <li key={p.id}>
                <label className="check">
                  <input type="checkbox" checked={!!ticked[p.id]} onChange={(e) => setTicked({ ...ticked, [p.id]: e.target.checked })} />
                  <span>{summaryOf(p)}</span>
                </label>
                <span className="art-meta">{p.origin}</span>
                {changeLines(p).length > 0 && <p className="art-meta kn-evidence">Writes {changeLines(p).join("; ")}</p>}
                {evidenceLines(p).length > 0 && <p className="art-meta kn-evidence">{evidenceLines(p).join("; ")}</p>}
                <div className="conn-actions">
                  <button type="button" className="index-rebuild" onClick={() => setOpen(open === p.id ? null : p.id)} aria-expanded={open === p.id}>Edit and accept</button>
                  <button type="button" className="inspect-close" disabled={busy} onClick={() => accept([p.id])}>Accept</button>
                  <button type="button" className="inspect-close" disabled={busy} onClick={() => reject([p.id])}>Reject</button>
                </div>
                {open === p.id && (
                  <div className="kn-edit">
                    {editableFields(p).map((f) => (
                      <div className="conn-field" key={f.key}>
                        <label className="settings-label" htmlFor={`edit-${p.id}-${f.key}`}>{f.key}</label>
                        <textarea id={`edit-${p.id}-${f.key}`} rows={3} value={(drafts[p.id] && drafts[p.id][f.key]) ?? f.value}
                                  onChange={(e) => setDrafts({ ...drafts, [p.id]: { ...drafts[p.id], [f.key]: e.target.value } })} />
                      </div>
                    ))}
                    {!editableFields(p).length && <p className="quiet">Nothing here is editable as text; edit the file after accepting.</p>}
                    <button type="button" className="settings-save" disabled={busy} onClick={() => accept([p.id])}>Accept with these edits</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <div className="conn-field">
        <label className="settings-label" htmlFor="kn-reason">Reason for rejecting (optional)</label>
        <input id="kn-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      {note && (
        <div role="status">
          <p className="settings-status">{note.summary}</p>
          {note.refused.map((r) => <p className="settings-warn" key={r.id}>{r.id}: {r.why}</p>)}
          {note.warnings.map((w) => <p className="settings-warn" key={w}>{w}</p>)}
        </div>
      )}
      {fault && <p className="fault" role="alert">{fault}</p>}
    </div>
  );
}
