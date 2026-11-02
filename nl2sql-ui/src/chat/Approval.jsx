import React, { useState } from "react";
import { listApprovals } from "../api/chat.js";
import { approvalState } from "./approval.js";

// The question needs someone to approve it first. "Check again" reads the approvals; once every request is granted,
// "Run it now" runs the question again with them.
export default function Approval({ needed, disabled, onRetry }) {
  const [state, setState] = useState({ state: "waiting", ids: [] });
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState(null);

  const check = async () => {
    setChecking(true);
    setError(null);
    try {
      const [granted, denied] = await Promise.all([listApprovals("granted"), listApprovals("denied")]);
      setState(approvalState(needed, [...granted, ...denied]));
    } catch (e) {
      setError(e.message);
    } finally {
      setChecking(false);
    }
  };

  return (
    <section className="approval-wait" aria-label="Waiting for approval">
      <h4>Waiting for approval</h4>
      <ul>{needed.map((n) => <li key={n.id}>{n.reason || n.question || "A reviewer has to approve this question."}</li>)}</ul>
      {state.state === "denied" && <p className="fault" role="status">A reviewer denied this request. It will not run.</p>}
      {state.state === "ready" && <p className="rate-saved" role="status">Approved. It can run now.</p>}
      <div className="clarify-actions">
        {state.state === "ready"
          ? <button type="button" className="ask" disabled={disabled} onClick={() => onRetry(state.ids)}>Run it now</button>
          : <button type="button" className="rate-btn" disabled={checking || state.state === "denied"} onClick={check}>Check again</button>}
      </div>
      {error && <p className="fault" role="alert">{error}</p>}
    </section>
  );
}
