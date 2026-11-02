import React from "react";
import { liveStations } from "./spine.js";

const TITLES = { plan: "Plan", checks: "Checks", sql: "SQL", rows: "Rows", cost: "Cost & time" };

// The steps of the question that is running, lit as each one finishes. The same spine the answer wears afterwards.
export default function Spine({ spine }) {
  const states = liveStations(spine);
  return (
    <ol className="spine chat-spine" aria-label="Progress of this turn">
      {Object.keys(TITLES).map((id) => (
        <li key={id} className="station" data-state={states[id]}>
          <span className="mark" aria-hidden="true" />
          <div className="station-head">
            <h3>{TITLES[id]}</h3>
            {states[id] === "busy" && <span className="station-time">working</span>}
          </div>
        </li>
      ))}
    </ol>
  );
}
