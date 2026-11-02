import React from "react";
import { stepStates } from "./flow.js";

// Where the person is in connecting a datasource: a numbered list, the current step marked for assistive tech.
export default function Steps({ current }) {
  return (
    <ol className="steps" aria-label="Steps">
      {stepStates(current).map((s) => (
        <li key={s.id} data-state={s.state} aria-current={s.state === "current" ? "step" : undefined}>{s.label}</li>
      ))}
    </ol>
  );
}
