import React, { useState } from "react";
import { NOTE_CHOICES, NOTE_MAX, canRate, correctionBody, feedbackBody, ratedLine } from "./feedback.js";
import { correct, rate } from "../api/chat.js";

// Rate an answer, under the run it belongs to. A rating saves at once; the note saves over it. A wrong answer can
// also carry the SQL that is right, which is filed as a proposal for review and changes nothing by itself.
export default function Feedback({ result }) {
  const [rating, setRating] = useState(null);
  const [note, setNote] = useState("");
  const [sql, setSql] = useState("");
  const [saved, setSaved] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

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
        <h4>Was this answer right?</h4>
        <div className="rate-buttons" role="group" aria-label="Rating">
          <button type="button" className="rate-btn" aria-pressed={rating === "up"} disabled={saving} onClick={() => send("up", note)}>Right</button>
          <button type="button" className="rate-btn" aria-pressed={rating === "down"} disabled={saving} onClick={() => send("down", note)}>Wrong</button>
        </div>
        {saved && <p className="rate-saved" role="status">{ratedLine(saved.rating, saved.note)}</p>}
      </div>
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
