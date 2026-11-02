import React, { useState } from "react";
import { parseMinutes, scheduleNote } from "./schedule.js";

// A dashboard refreshes itself on a schedule, under the identity of whoever set it. The server holds the shortest
// interval it allows and answers a shorter one with a sentence, which the page shows as it is.
export default function Schedule({ schedule, busy, onSave }) {
  const [text, setText] = useState(schedule ? String(schedule.every_minutes) : "");
  const minutes = parseMinutes(text);
  return (
    <div className="dash-schedule">
      <p className="page-note">{scheduleNote(schedule)}</p>
      <div className="field">
        <label htmlFor="dash-every">Every (minutes)</label>
        <input id="dash-every" className="dash-input dash-short" inputMode="numeric" value={text}
          onChange={(e) => setText(e.target.value)} />
        <button type="button" className="index-rebuild is-primary" disabled={busy || minutes === null} onClick={() => onSave(minutes)}>
          Save schedule
        </button>
        {schedule && <button type="button" className="index-rebuild" disabled={busy} onClick={() => { setText(""); onSave(null); }}>Stop</button>}
      </div>
    </div>
  );
}
