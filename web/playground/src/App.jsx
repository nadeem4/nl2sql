import React, { useEffect, useRef, useState } from "react";
import SchemaPanel from "./SchemaPanel.jsx";
import IndexPanel from "./IndexPanel.jsx";
import { needsRebuild } from "./indexHealth.js";
import Run from "./Panes.jsx";
import Settings from "./Settings.jsx";
import RetrievalInspector from "./Retrieval.jsx";
import { createRouter, hashFor, navItems, pageFor, pageFromHash } from "./router.js";
import { deniedTables, planTables } from "./run.js";

// The banner claims recorded answers only when the server loaded some.
function replayNote(recorded, total, canSet) {
  const fix = canSet ? "add an API key under Settings or restart with --api-key" : "restart with --api-key";
  if (!recorded) {
    return `No API key found, and replay mode has no recorded answers, so no question can be answered. To ask questions, ${fix}.`;
  }
  return `No API key found. ${recorded} of ${total} guided questions answer from recorded model responses; for any other question, ${fix}.`;
}

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

// The address bar says which page is open, so a reload and a shared link both
// work and Back walks the pages you visited.
function useRoute() {
  const [page, setPage] = useState(() => pageFromHash(window.location.hash));
  useEffect(() => {
    const router = createRouter(window);
    // The hash can change between the first render and this effect.
    setPage(router.page());
    const drop = router.subscribe(setPage);
    return () => {
      drop();
      router.stop();
    };
  }, []);
  return page;
}

function focusById(id) {
  const target = document.getElementById(id);
  if (target) target.focus();
}

export default function App() {
  const page = useRoute();
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
  const [index, setIndex] = useState(null);
  const [indexError, setIndexError] = useState(null);
  const [retrieval, setRetrieval] = useState(null);
  const [retrievalError, setRetrievalError] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const runRef = useRef(null);
  const pageRef = useRef(null);
  const firstPage = useRef(true);

  useEffect(() => {
    getJson("/api/meta")
      .then((m) => {
        setMeta(m);
        if (m.roles.length) setRole(m.roles.includes("admin") ? "admin" : m.roles[0]);
      })
      .catch((e) => setError(e.message));
    getJson("/api/schema").then(setSchema).catch((e) => setError(e.message));
    getJson("/api/settings").then(setSettings).catch((e) => setSettingsError(e.message));
    getJson("/api/index").then(setIndex).catch((e) => setIndexError(e.message));
    getJson("/api/retrieval").then(setRetrieval).catch((e) => setRetrievalError(e.message));
    // Without it the rating control stays hidden; nothing else depends on it.
    getJson("/api/feedback").then(setFeedback).catch(() => {});
  }, []);

  // A new page starts at its top, with the keyboard on it: the browser does
  // neither of those for a view the hash swapped out.
  useEffect(() => {
    if (firstPage.current) {
      firstPage.current = false;
      return;
    }
    window.scrollTo(0, 0);
    if (pageRef.current) pageRef.current.focus();
  }, [page]);

  // While a rebuild runs, follow its steps; when it ends, re-read the schema,
  // which the rebuild re-read from the database too.
  const rebuilding = index && index.job.state === "running";
  useEffect(() => {
    if (!rebuilding) return undefined;
    const timer = setInterval(() => {
      getJson("/api/index")
        .then((next) => {
          setIndex(next);
          if (next.job.state !== "running") {
            getJson("/api/schema").then(setSchema).catch(() => {});
          }
        })
        .catch((e) => setIndexError(e.message));
    }, 1000);
    return () => clearInterval(timer);
  }, [rebuilding]);

  const rebuildIndex = async (enrich) => {
    const response = await fetch("/api/index/rebuild", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enrich }),
    });
    const reply = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof reply.detail === "string" ? reply.detail : `The server answered ${response.status}.`);
    }
    setIndex(reply);
  };

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
  const indexBroken = index && needsRebuild(index.health) && index.job.state !== "running";
  const onAsk = page === "ask";
  const current = pageFor(page);
  const nav = navItems(page, {
    settings: settings && !settings.available,
    retrieval: retrieval && !retrieval.available,
  });

  return (
    <div className="app">
      {/* A button, not a fragment link: the hash belongs to the router. */}
      <button className="skip" onClick={() => focusById(onAsk ? "run" : "page")}>
        {onAsk ? "Skip to the run" : "Skip to the page"}
      </button>

      <header className="topbar">
        <div className="topbar-line">
          {/* The product mark, not the page's heading: the page title is. */}
          <p className="wordmark">
            <span className="mono">nl2sql</span> playground
          </p>
          {meta && (
            <p className={`mode mode-${meta.mode}`}>
              <strong>{replay ? "Replay mode." : "Live mode."}</strong>{" "}
              {replay
                ? replayNote(meta.recorded_questions, (meta.questions || []).length, canSet)
                : "Questions go to the configured model."}
            </p>
          )}
        </div>
        <nav className="nav" aria-label="Playground pages">
          <ul>
            {nav.map((item) => (
              <li key={item.id}>
                <a
                  id={`nav-${item.id}`}
                  className="nav-link"
                  href={item.href}
                  aria-current={item.current ? "page" : undefined}
                >
                  {item.label}
                  {item.off && <span className="nav-off">off</span>}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      {indexBroken && (
        <p className="index-warning" id="index-warning" role="alert">
          <strong>{index.health.status === "stale" ? "The search index is out of date." : "The search index is empty."}</strong>{" "}
          {index.health.status === "stale"
            ? "Answers may use an older schema. "
            : "Every question will fail until it is rebuilt. "}
          {!index.rebuild.available ? (
            <>Run <code>nl2sql --env demo index</code> in the demo directory.</>
          ) : onAsk ? (
            <button className="linkish" onClick={() => focusById("index-rebuild")}>Rebuild it</button>
          ) : (
            <a href={hashFor("ask")}>Rebuild it</a>
          )}
        </p>
      )}

      <main className="page" id="page" ref={pageRef} tabIndex={-1} aria-labelledby="page-title">
        <div className="page-head">
          <h1 id="page-title">{current.title}</h1>
          <p className="page-lede">{current.description}</p>
          {current.note && <p className="page-note">{current.note}</p>}
        </div>

        {onAsk && (
          <div className="layout">
            <section className="composer" aria-labelledby="ask-heading">
              <h2 id="ask-heading" className="visually-hidden">Ask</h2>
              {/* The page title above already says Ask; this names the database. */}
              <label className="question-label" htmlFor="question">
                Your question for the {meta ? meta.dataset : "demo"} database
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
              <IndexPanel index={index} error={indexError} onRebuild={rebuildIndex} />
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
                feedback={feedback}
              />
            </section>
          </div>
        )}

        {page === "settings" && (
          <div className="sheet" id="settings-panel">
            <Settings settings={settings} error={settingsError} onSaved={settingsSaved}
              recorded={meta ? meta.recorded_questions : 0} />
          </div>
        )}

        {page === "retrieval" && (
          <div className="sheet" id="retrieval-panel">
            <RetrievalInspector options={retrieval} error={retrievalError} question={question} />
          </div>
        )}
      </main>
    </div>
  );
}
