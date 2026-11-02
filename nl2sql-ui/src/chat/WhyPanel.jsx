import React from "react";
import { whySections } from "./why.js";

// Why this answer: the tables, joins, filters, terms and rules the engine used. The planner note is the model's own
// words, so it says so.
export default function WhyPanel({ result }) {
  const sections = whySections(result);
  if (!sections.length) return <p className="page-note">No explanation was kept for this answer.</p>;
  return (
    <div className="why">
      {sections.map((s, i) => (
        <dl key={i} className="why-step">
          {sections.length > 1 && <dt className="why-source">Question {i + 1}</dt>}
          {s.parts.map((p) => (
            <React.Fragment key={p.label}>
              <dt>{p.label}{p.model && <span className="kind-tag" data-kind="model">written by the model</span>}</dt>
              <dd>
                <ul>{p.items.map((item, j) => <li key={j}>{item}</li>)}</ul>
              </dd>
            </React.Fragment>
          ))}
        </dl>
      ))}
    </div>
  );
}
