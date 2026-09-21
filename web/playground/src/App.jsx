import React, { useEffect, useState } from "react";
import SchemaPanel from "./SchemaPanel.jsx";
import { PlanPane, RowsPane, SqlPane, UsagePane, ValidationPane } from "./Panes.jsx";

const REPLAY_NOTE =
  "No API key found. The guided questions run from recorded model responses; " +
  "set OPENAI_API_KEY for free-form questions.";

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response.json();
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [schema, setSchema] = useState(null);
  const [question, setQuestion] = useState("");
  const [role, setRole] = useState("admin");
  const [planOnly, setPlanOnly] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getJson("/api/meta")
      .then((m) => {
        setMeta(m);
        if (m.roles.length) setRole(m.roles.includes("admin") ? "admin" : m.roles[0]);
      })
      .catch((e) => setError(e.message));
    getJson("/api/schema").then(setSchema).catch((e) => setError(e.message));
  }, []);

  const ask = async (text) => {
    const asked = (text === undefined ? question : text).trim();
    if (!asked || busy) return;
    setQuestion(asked);
    setBusy(true);
    setError(null);
    try {
      setResult(
        await getJson("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: asked, role, execute: !planOnly }),
        })
      );
    } catch (e) {
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const sub = result && result.sub_queries && result.sub_queries[0];

  return (
    <div className="app">
      <header>
        <h1>
          nl2sql playground
          {meta && <span className="dataset">{meta.dataset}</span>}
        </h1>
        {meta && (
          <p className={`mode mode-${meta.mode}`}>
            <strong>{meta.mode} mode.</strong> {meta.mode === "replay" ? REPLAY_NOTE : "Questions go to the configured provider."}
          </p>
        )}
      </header>

      <div className="columns">
        <div className="left">
          <section className="panel ask">
            <h2>Ask</h2>
            <textarea
              id="question"
              rows={3}
              value={question}
              placeholder="Ask a question about the database..."
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask();
              }}
            />
            <div className="controls">
              <label htmlFor="role-select">Role</label>
              <select id="role-select" value={role} onChange={(e) => setRole(e.target.value)}>
                {(meta ? meta.roles : ["admin"]).map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              <label className="checkbox" htmlFor="plan-only">
                <input
                  id="plan-only"
                  type="checkbox"
                  checked={planOnly}
                  onChange={(e) => setPlanOnly(e.target.checked)}
                />
                Plan only
              </label>
              <button className="primary" onClick={() => ask()} disabled={busy || !question.trim()}>
                {busy ? "Thinking..." : "Ask"}
              </button>
            </div>
            {meta && meta.questions.length > 0 && (
              <>
                <h3>Guided questions</h3>
                <div className="chips">
                  {meta.questions.map((q) => (
                    <button key={q} className="chip" onClick={() => ask(q)} disabled={busy}>
                      {q}
                    </button>
                  ))}
                </div>
              </>
            )}
          </section>

          <SchemaPanel schema={schema} />
        </div>

        <div className="right">
          {error && <p className="panel bad">{error}</p>}
          {result && result.replay_miss && (
            <p className="panel warn">
              This question has no recording. Set an API key and restart for live mode.
            </p>
          )}
          {result && !result.replay_miss && result.status && (
            <p className={`status status-${result.status}`}>status: {result.status}</p>
          )}
          <PlanPane sub={sub} />
          <ValidationPane sub={sub} result={result} />
          <SqlPane sub={sub} />
          <RowsPane sub={sub} result={result} />
          {result && (
            <UsagePane usage={result.usage} timings={result.timings} replay={meta && meta.mode === "replay"} />
          )}
        </div>
      </div>
    </div>
  );
}
