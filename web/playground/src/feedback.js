// Pure helpers for the answer feedback control. The server records the run
// itself (question, role, SQL, models) from its own copy of the answer; the
// page sends only which run, the rating and an optional note.

export const NOTE_MAX = 280;

export const NOTE_CHOICES = ["Wrong number", "Wrong table", "Wrong filter", "Missing rows"];

// A run can be rated once it is back, answered something, and has an id.
export function canRate(result, busy) {
  return !busy && !!result && !!result.trace_id && !result.replay_miss;
}

export function feedbackBody(result, rating, note) {
  if (rating !== "up" && rating !== "down") throw new Error(`Not a rating: ${rating}`);
  const text = (note || "").trim().slice(0, NOTE_MAX);
  return { trace_id: result.trace_id, rating, note: text || null };
}

export function ratedLine(rating, note) {
  const what = rating === "up" ? "good answer" : "wrong answer";
  return note ? `Saved: ${what}, with the note “${note}”.` : `Saved: ${what}.`;
}
