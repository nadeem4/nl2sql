import React, { useEffect, useState } from "react";

// The question box. In a new conversation it offers the datasources' starter questions and, with more than one
// datasource, a choice of which to ask; once a conversation exists the datasource is fixed.
export default function Composer({ busy, canStop, fresh, datasources, datasource, onDatasource, starter, onSend, onStop }) {
  const [text, setText] = useState("");
  useEffect(() => { if (starter) setText(starter); }, [starter]);

  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    onSend(q);
  };
  const chosen = datasources.find((d) => d.id === datasource) || datasources[0];
  const questions = fresh && chosen ? (chosen.sample_questions || []).slice(0, 4) : [];

  return (
    <section className="composer chat-composer" aria-label="Ask">
      {questions.length > 0 && (
        <ul className="chat-starters" aria-label="Starter questions">
          {questions.map((q) => (
            <li key={q}><button type="button" className="rate-choice" disabled={busy} onClick={() => onSend(q)}>{q}</button></li>
          ))}
        </ul>
      )}
      <label className="question-label" htmlFor="chat-question">Your question</label>
      <textarea id="chat-question" rows={2} value={text} placeholder="Which genre sells the most tracks?"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }} />
      <div className="controls">
        {fresh && datasources.length > 1 && (
          <div className="field">
            <label htmlFor="chat-datasource">Datasource</label>
            <select id="chat-datasource" value={chosen ? chosen.id : ""} onChange={(e) => onDatasource(e.target.value)}>
              {datasources.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
            </select>
          </div>
        )}
        {busy
          ? (canStop
            ? <button type="button" className="ask" onClick={onStop}>Stop</button>
            : <button type="button" className="ask" disabled>Working</button>)
          : <button type="button" className="ask" disabled={!text.trim()} onClick={submit}>Ask</button>}
      </div>
    </section>
  );
}
