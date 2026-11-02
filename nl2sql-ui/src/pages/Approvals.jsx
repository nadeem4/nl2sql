import React, { useCallback, useEffect, useState } from "react";
import { denyApproval, grantApproval, listApprovals } from "../api/chat.js";
import SqlCard from "../components/SqlCard.jsx";
import { planSections } from "../lib/plan.js";
import { ownRequest } from "../chat/approval.js";

// One request waiting for a decision: who asked, what the plan reads, the SQL it would run and why it stopped.
function Request({ record, busy, own, onGrant, onDeny }) {
  const r = record.request;
  const sections = planSections(r.plan);
  return (
    <li className="approval">
      <h3 className="approval-q">{r.question}</h3>
      <p className="page-note">Asked by {r.asker_user_id || "an anonymous caller"}{r.datasource_id ? ` on ${r.datasource_id}` : ""}. {r.reason}</p>
      {sections.length > 0 && (
        <dl className="plan">
          {sections.map((s) => (
            <div key={s.label} className="clause"><dt>{s.label}</dt><dd>{s.items.join("; ")}</dd></div>
          ))}
        </dl>
      )}
      {r.sql && <SqlCard sql={r.sql} plan={r.plan} />}
      {own
        ? <p className="page-note">You asked this. Someone with an approver role has to decide it.</p>
        : (
          <div className="clarify-actions">
            <button type="button" className="ask" disabled={busy} onClick={() => onGrant(record.id)}>Grant</button>
            <button type="button" className="rate-btn" disabled={busy} onClick={() => onDeny(record.id)}>Deny</button>
          </div>
        )}
    </li>
  );
}

// The pending approvals the caller may decide. A decision removes the request from the list.
// `callerId` is the signed-in user's id from /meta (none for a hosted visitor, and until /meta answers); the server still refuses a decision from anyone else.
export default function Approvals({ callerId }) {
  const [records, setRecords] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(
    () => listApprovals("pending").then(setRecords).catch((e) => { setRecords([]); setError(e.message); }), []);
  useEffect(() => { load(); }, [load]);

  const decide = async (call, id) => {
    setBusy(true);
    setError(null);
    try {
      await call(id);
      await load();
    } catch (e) {
      setError(e.fix ? `${e.message} ${e.fix}` : e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sheet approvals">
      {error && <p className="fault" role="alert">{error}</p>}
      {records && records.length === 0 && <p className="page-note">Nothing is waiting for approval.</p>}
      <ul className="approval-list">
        {(records || []).map((record) => (
          <Request key={record.id} record={record} busy={busy} own={ownRequest(record, callerId)}
            onGrant={(id) => decide(grantApproval, id)} onDeny={(id) => decide((i) => denyApproval(i), id)} />
        ))}
      </ul>
    </div>
  );
}
