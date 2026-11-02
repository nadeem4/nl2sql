import React, { useEffect, useState } from "react";
import { deleteAnswer, listAnswers, runAnswer } from "../api/answers.js";
import { answerDate, rerunApprovalIds, runView } from "../chat/answers.js";
import RowsTable from "../outputs/RowsTable.jsx";

// The answers a person kept with "Yes" in chat. Run answers the saved plan again as the viewer, so what they may read
// and any approval apply; no model is called. Delete forgets one. A list of one's own: nobody else sees it.
function Shown({ shown }) {
  if (!shown) return null;
  const view = runView(shown);
  if (view.kind === "rows") return <RowsTable result={shown} />;
  return (
    <div className="dash-note" data-kind={view.kind} role={view.kind === "error" ? "alert" : undefined}>
      <p>{view.kind === "approval" ? "This answer is waiting for an approval." : view.message}</p>
      {view.kind === "approval" && (
        <p className="dash-note-sub">Someone with the right role grants it in <a href="#/approvals">Approvals</a>, then use Run again.</p>
      )}
    </div>
  );
}

export default function MyAnswers() {
  const [answers, setAnswers] = useState(null);
  const [shown, setShown] = useState({});
  const [busy, setBusy] = useState(() => new Set());
  const [error, setError] = useState(null);

  useEffect(() => {
    listAnswers().then(setAnswers).catch((e) => { setAnswers([]); setError(e); });
  }, []);

  const guard = async (id, work) => {
    setBusy((prev) => new Set(prev).add(id));
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e);
    } finally {
      setBusy((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  };
  const run = (id, approvalIds) => guard(id, async () => {
    const result = await runAnswer(id, approvalIds);
    setShown((prev) => ({ ...prev, [id]: result }));
  });
  const remove = (answer) => {
    if (!window.confirm(`Delete "${answer.question}" from My answers?`)) return;
    guard(answer.id, async () => {
      await deleteAnswer(answer.id);
      setAnswers((prev) => prev.filter((a) => a.id !== answer.id));
      setShown(({ [answer.id]: _gone, ...rest }) => rest);
    });
  };

  const count = answers ? answers.length : 0;
  return (
    <details className="fold my-answers">
      <summary>My answers{answers && <span className="fold-meta">{count}</span>}</summary>
      {error && <p className="rows-error" role="alert">{error.message || String(error)}{error.fix ? ` ${error.fix}` : ""}</p>}
      {answers && count === 0 && !error && (
        <p className="page-note">Nothing kept yet. Choose Yes under an answer in chat to keep it here.</p>
      )}
      {count > 0 && (
        <ul className="my-answers-list">
          {answers.map((a) => (
            <li key={a.id} className="my-answer">
              <div className="my-answer-head">
                <p className="my-answer-q">{a.question}</p>
                <span className="dash-meta">{[a.datasource_id, answerDate(a)].filter(Boolean).join(" · ")}</span>
              </div>
              <div className="dash-row">
                <button type="button" className="index-rebuild" disabled={busy.has(a.id)} onClick={() => run(a.id, rerunApprovalIds(shown[a.id]))}>
                  {shown[a.id] ? "Run again" : "Run"}
                </button>
                <button type="button" className="index-rebuild dash-danger" disabled={busy.has(a.id)} onClick={() => remove(a)}>Delete</button>
              </div>
              <Shown shown={shown[a.id]} />
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
