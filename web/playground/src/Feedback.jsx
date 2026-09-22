import React, { useState } from "react";
import { NOTE_CHOICES, NOTE_MAX, canRate, feedbackBody, ratedLine } from "./feedback.js";

// Rate the answer, under the run it belongs to. A rating saves at once; the
// note is optional and saves over it. Keyed by trace id, so a new run starts
// unrated.
export default function Feedback({ result, busy, options }) {
  const [rating, setRating] = useState(null);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  if (!canRate(result, busy) || !options) return null;
  if (!options.available) {
    return <p className="rate-off" id="feedback">Rating answers is off. {options.reason}</p>;
  }

  const send = async (nextRating, nextNote) => {
    setSaving(true);
    setError(null);
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedbackBody(result, nextRating, nextNote)),
      });
      const reply = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof reply.detail === "string" ? reply.detail : `The server answered ${response.status}.`);
      }
      setRating(nextRating);
      setSaved(reply.saved);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="rate" id="feedback" aria-labelledby="feedback-title">
      <div className="rate-head">
        <h3 id="feedback-title">Was this answer right?</h3>
        <div className="rate-buttons" role="group" aria-labelledby="feedback-title">
          <button id="feedback-up" className="rate-btn" aria-pressed={rating === "up"} disabled={saving}
            onClick={() => send("up", note)}>
            <span aria-hidden="true">{"\u{1F44D}"}</span> Right
          </button>
          <button id="feedback-down" className="rate-btn" aria-pressed={rating === "down"} disabled={saving}
            onClick={() => send("down", note)}>
            <span aria-hidden="true">{"\u{1F44E}"}</span> Wrong
          </button>
        </div>
        {saved && <p className="rate-saved" role="status">{ratedLine(saved.rating, saved.note)}</p>}
      </div>
      {rating && (
        <form className="rate-note" onSubmit={(e) => { e.preventDefault(); send(rating, note); }}>
          <label htmlFor="feedback-note">Add a note (optional)</label>
          <div className="rate-choices">
            {NOTE_CHOICES.map((c) => (
              <button key={c} type="button" className="rate-choice" aria-pressed={note === c}
                onClick={() => setNote(c)}>{c}</button>
            ))}
          </div>
          <div className="rate-entry">
            <input id="feedback-note" type="text" maxLength={NOTE_MAX} value={note}
              placeholder="What was wrong, in a few words" onChange={(e) => setNote(e.target.value)} />
            <button id="feedback-save" type="submit" className="rate-btn" disabled={saving || !note.trim()}>
              Save note
            </button>
          </div>
        </form>
      )}
      {error && <p className="fault" role="alert">{error}</p>}
      <p className="rate-help">
        Saved in this project with the question, role and SQL, never the rows. <code>nl2sql feedback stats</code> reports the rates.
      </p>
    </section>
  );
}
