import React, { useId, useState } from "react";
import { rememberBody } from "./clarify.js";
import { saveChoice } from "../api/chat.js";

// The model could not tell which meaning the question has. Each reading shows what it would do; picking one runs the
// turn again with it, and "Remember this meaning" also files it as a term proposal for review.
export default function Clarify({ clarification, disabled, onPick }) {
  const termId = useId();
  const [term, setTerm] = useState("");
  const [filed, setFiled] = useState(null);
  const [error, setError] = useState(null);

  const remember = async (option) => {
    setError(null);
    try {
      await saveChoice(rememberBody(clarification, option, term));
      setFiled(option.id);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <section className="clarify" aria-label="Which meaning do you want?">
      <p className="clarify-q">{clarification.question}</p>
      <ul className="clarify-options">
        {(clarification.options || []).map((option) => (
          <li key={option.id} className="clarify-option">
            <p className="clarify-label">{option.label}</p>
            <div className="clarify-actions">
              <button type="button" className="ask" disabled={disabled} onClick={() => onPick(option)}>Use this reading</button>
              <button type="button" className="rate-btn" disabled={disabled || filed === option.id} onClick={() => remember(option)}>
                {filed === option.id ? "Sent for review" : "Remember this meaning"}
              </button>
            </div>
          </li>
        ))}
      </ul>
      <label htmlFor={termId}>Term to remember (optional)</label>
      <input id={termId} type="text" value={term} onChange={(e) => setTerm(e.target.value)} />
      {error && <p className="fault" role="alert">{error}</p>}
    </section>
  );
}
