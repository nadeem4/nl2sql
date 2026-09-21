import React, { useEffect, useRef, useState } from "react";
import SchemaPanel from "./SchemaPanel.jsx";
import Run from "./Panes.jsx";
import Settings from "./Settings.jsx";
import { deniedTables, planTables } from "./run.js";

const REPLAY_NOTE = "No API key found. The guided questions run from recorded model responses; ";
const REPLAY_FIX_HERE = "add a key under Settings for free-form questions.";
const REPLAY_FIX_RESTART = "set OPENAI_API_KEY and restart for free-form questions.";

const DEBUG_KEY = "nl2sql.playground.debug";

// Debug detail is on unless this browser turned it off. Storage can be missing
// or throw (private windows, blocked site data); the default then applies.
function readDebug() {
  try {
    return window.localStorage.getItem(DEBUG_KEY) !== "off";
  } catch {
    return true;
  }
}

function writeDebug(on) {
  try {
    window.localStorage.setItem(DEBUG_KEY, on ? "on" : "off");
  } catch {
    /* not persisted; the toggle still works for this visit */
  }
}

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
  const [debug, setDebug] = useState(readDebug);
  const [result, setResult] = useState(null);
  const [asked, setAsked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [settings, setSettings] = useState(null);
  const [settingsError, setSettingsError] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const runRef = useRef(null);

  useEffect(() => {
    getJson("/api/meta")
      .then((m) => {
        setMeta(m);
        if (m.roles.length) setRole(m.roles.includes("admin") ? "admin" : m.roles[0]);
      })
      .catch((e) => setError(e.message));
    getJson("/api/schema").then(setSchema).catch((e) => setError(e.message));
    getJson("/api/settings").then(setSettings).catch((e) => setSettingsError(e.message));
  }, []);

  // A saved key can turn replay into live; the mode line follows the server.
  const settingsSaved = (next) => {
    setSettings(next);
    setMeta((m) => (m ? { ...m, mode: next.mode } : m));
  };

  const ask = async (text) => {
    const q = (text === undefined ? question : text).trim();
    if (!q || busy) return;
    setQuestion(q);
    setAsked({ question: q, role, planOnly });
    setBusy(true);
    setError(null);
    setResult(null);
    // On a single-column layout the run sits below the fold; bring it up.
    const run = runRef.current;
    if (run && run.getBoundingClientRect().top > window.innerHeight * 0.6) {
      const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      run.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
    }
    try {
      setResult(
        await getJson("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q, role, execute: !planOnly }),
        })
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleDebug = (on) => {
    setDebug(on);
    writeDebug(on);
  };

  const sub = result && result.sub_queries && result.sub_queries[0];
  const used = planTables(sub && sub.plan);
  const denied = deniedTables(result && result.errors);
  const replay = meta && meta.mode === "replay";
  const canSet = settings && settings.available;

  return (
    <div className="app">
      <a className="skip" href="#run">Skip to the run</a>
      <header className="topbar">
        <h1 className="wordmark">
          <span className="mono">nl2sql</span> playground
        </h1>
        {meta && (
          <p className={`mode mode-${meta.mode}`}>
            <strong>{replay ? "Replay mode." : "Live mode."}</strong>{" "}
            {replay ? REPLAY_NOTE + (canSet ? REPLAY_FIX_HERE : REPLAY_FIX_RESTART) : "Questions go to the configured model."}
          </p>
        )}
        <button
          id="settings-toggle"
          className="settings-toggle"
          aria-expanded={settingsOpen}
          aria-controls="settings-panel"
          onClick={() => setSettingsOpen(!settingsOpen)}
        >
          Settings
          {settings && !settings.available && <span className="settings-toggle-off">off</span>}
        </button>
      </header>

      {settingsOpen && (
        <section id="settings-panel" className="settings" aria-labelledby="settings-heading">
          <h2 id="settings-heading" className="visually-hidden">Settings</h2>
          <Settings settings={settings} error={settingsError} onSaved={settingsSaved} />
        </section>
      )}

      <main className="layout">
        <section className="composer" aria-labelledby="ask-heading">
          <h2 id="ask-heading" className="visually-hidden">Ask</h2>
          <label className="question-label" htmlFor="question">
            Ask the {meta ? meta.dataset : "demo"} database a question
          </label>
          <textarea
            id="question"
            rows={2}
            value={question}
            placeholder="Which genre sells the most tracks?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask();
            }}
          />
          <div className="controls">
            <div className="field">
              <label htmlFor="role-select">Ask as</label>
              <select id="role-select" value={role} onChange={(e) => setRole(e.target.value)}>
                {(meta ? meta.roles : ["admin"]).map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
            <label className="check" htmlFor="plan-only">
              <input id="plan-only" type="checkbox" checked={planOnly} onChange={(e) => setPlanOnly(e.target.checked)} />
              Plan only
            </label>
            <label className="check" htmlFor="debug-toggle">
              <input id="debug-toggle" type="checkbox" checked={debug} onChange={(e) => toggleDebug(e.target.checked)} />
              Debug
              <span className="hint">per-node tokens and time</span>
            </label>
            <button className="ask" onClick={() => ask()} disabled={busy || !question.trim()}>
              {busy ? "Asking" : "Ask"}
              <kbd aria-hidden="true">Ctrl Enter</kbd>
            </button>
          </div>
          {meta && meta.questions.length > 0 && (
            <section className="guided" aria-labelledby="guided-heading">
              <h3 id="guided-heading">Or try a guided question</h3>
              <ul>
                {meta.questions.map((q) => (
                  <li key={q}>
                    <button className="guided-q" onClick={() => ask(q)} disabled={busy}>{q}</button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </section>

        <aside className="rail">
          <SchemaPanel schema={schema} used={used} denied={denied} role={asked && asked.role} />
        </aside>

        <section className="run" id="run" ref={runRef} aria-labelledby="run-heading" tabIndex={-1}>
          <h2 id="run-heading" className="visually-hidden">The run</h2>
          <Run
            asked={asked}
            result={result}
            sub={sub}
            busy={busy}
            error={error}
            debug={debug}
            replay={replay}
          />
        </section>
      </main>
    </div>
  );
}
