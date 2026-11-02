import React, { useState, useSyncExternalStore } from "react";
import { NOTE_CHOICES, NOTE_MAX, canRate, correctionBody, feedbackBody, ratedLine } from "./feedback.js";
import { correct, rate } from "../api/chat.js";
import { saveAnswer } from "../api/answers.js";
import { createHintStore, describeSaveError, saveBody, savedLine } from "./answers.js";

// The browser's own storage, or nothing where it is blocked: the hint helpers cope with both.
const storage = () => { try { return window.localStorage; } catch { return undefined; } };
// One store for the page: the first click under any answer hides the hint under all of them.
const hintStore = createHintStore(storage());

// "Did this answer it?" under the run it belongs to. Yes keeps the answer in My answers (and the server rates the run
// up and may file it for review); Not quite rates it down and opens the note, where a wrong answer can also carry the
// SQL that is right, filed as a proposal for review that changes nothing by itself. `conversationId` and `stepRef`
// name the step for Yes; without them Yes is a plain rating.
export default function Feedback({ result, conversationId = null, stepRef = null }) {
  const [rating, setRating] = useState(null);
  const [note, setNote] = useState("");
  const [sql, setSql] = useState("");
  const [saved, setSaved] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const hint = useSyncExternalStore(hintStore.subscribe, hintStore.visible);

  if (!canRate(result, false)) return null;

  const run = async (work) => {
    setSaving(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };
  const choose = (go) => {
    hintStore.dismiss();
    go();
  };
  const yes = () => run(async () => {
    if (!conversationId || !stepRef) return send("up", note);
    try {
      setSaved({ line: savedLine(await saveAnswer(saveBody(result, conversationId, stepRef, note))) });
      setRating("up");
    } catch (e) {
      throw new Error(describeSaveError(e));
    }
  });
  const send = (next, text) => run(async () => {
    const reply = await rate(feedbackBody(result, next, text));
    setRating(next);
    setSaved(reply && reply.saved ? reply.saved : { rating: next, note: text || null });
  });
  const sendCorrection = (e) => {
    e.preventDefault();
    run(async () => setProposal(await correct(correctionBody(result, sql, note))));
  };
  const id = result.trace_id;

  return (
    <section className="rate chat-rate" aria-label="Rate this answer">
      <div className="rate-head">
        <h4>Did this answer it?</h4>
        <div className="rate-buttons" role="group" aria-label="Rating">
          <button type="button" className="rate-btn" aria-pressed={rating === "up"} disabled={saving} onClick={() => choose(yes)}>Yes</button>
          <button type="button" className="rate-btn" aria-pressed={rating === "down"} disabled={saving} onClick={() => choose(() => send("down", note))}>Not quite</button>
        </div>
        {saved && <p className="rate-saved" role="status">{saved.line || ratedLine(saved.rating, saved.note)}</p>}
      </div>
      {hint && <p className="rate-hint">Yes keeps this answer in My answers. Not quite lets you say what was wrong.</p>}
      {rating === "down" && !proposal && (
        <form className="rate-note" onSubmit={sendCorrection}>
          <div className="rate-choices">
            {NOTE_CHOICES.map((c) => (
              <button key={c} type="button" className="rate-choice" aria-pressed={note === c} onClick={() => setNote(c)}>{c}</button>
            ))}
          </div>
          <label htmlFor={`note-${id}`}>What was wrong (optional)</label>
          <input id={`note-${id}`} type="text" maxLength={NOTE_MAX} value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="rate-btn" disabled={saving || !note.trim()} onClick={() => send("down", note)}>Save note</button>
          <label htmlFor={`sql-${id}`}>The SQL that is right (optional)</label>
          <textarea id={`sql-${id}`} rows={3} spellCheck={false} value={sql} onChange={(e) => setSql(e.target.value)} />
          <button type="submit" className="rate-btn" disabled={saving || !sql.trim()}>Send for review</button>
        </form>
      )}
      {proposal && <p className="rate-saved" role="status">Sent for review{proposal.id ? ` as ${proposal.id}` : ""}. Nothing changes until someone approves it.</p>}
      {error && <p className="fault" role="alert">{error}</p>}
    </section>
  );
}
