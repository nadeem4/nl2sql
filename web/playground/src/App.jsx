import React, { useEffect, useRef, useState } from "react";
import SchemaPanel from "./SchemaPanel.jsx";
import IndexPanel from "./IndexPanel.jsx";
import { needsRebuild } from "./indexHealth.js";
import Run from "./Panes.jsx";
import Settings from "./Settings.jsx";
import Pipeline from "./Pipeline.jsx";
import RetrievalInspector from "./Retrieval.jsx";
import { answeredDatasources, datasourceNames } from "./datasources.js";
import { guidedGroups } from "./questions.js";
import { NO_KEY_REASON, hostedNote, needsKey } from "./firstRun.js";
import { askHeaders, readKeys, writeKeyFor } from "./hostedKey.js";
import { readModels, writeModel } from "./hostedModels.js";
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
  if (!response.ok) {
    // A refusal carries the server's own sentence -- no key, a bad key, a
    // limit reached -- and that is what the page should show, not a status.
    const reply = await response.json().catch(() => ({}));
    throw new Error(
      typeof reply.detail === "string" ? reply.detail : `${url} returned ${response.status}`,
    );
  }
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
  const [pipeline, setPipeline] = useState(null);
  const [pipelineError, setPipelineError] = useState(null);
  const [feedback, setFeedback] = useState(null);
  // Which database the schema panel is showing. Null until something picks
  // one, when the server serves the demo's own.
  const [datasource, setDatasource] = useState(null);
  // The hosted demo's keys and step choices: this tab's, never the server's.
  const [apiKeys, setApiKeys] = useState(() => readKeys(window.sessionStorage));
  const [stepModels, setStepModels] = useState(() => readModels(window.sessionStorage));
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
    getJson("/api/settings").then(setSettings).catch((e) => setSettingsError(e.message));
    getJson("/api/index").then(setIndex).catch((e) => setIndexError(e.message));
    getJson("/api/retrieval").then(setRetrieval).catch((e) => setRetrievalError(e.message));
    getJson("/api/pipeline").then(setPipeline).catch((e) => setPipelineError(e.message));
    // Without it the rating control stays hidden; nothing else depends on it.
    getJson("/api/feedback").then(setFeedback).catch(() => {});
  }, []);

  // The schema panel follows whichever database is picked; with none picked
  // the server serves the demo's own.
  const schemaUrl = datasource ? `/api/schema?datasource=${encodeURIComponent(datasource)}` : "/api/schema";
  useEffect(() => {
    // Two databases picked quickly are two requests in flight; only the one
    // the rail is still showing may land.
    let current = true;
    getJson(schemaUrl)
      .then((next) => current && setSchema(next))
      .catch((e) => current && setError(e.message));
    return () => {
      current = false;
    };
  }, [schemaUrl]);

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
            getJson(schemaUrl).then(setSchema).catch(() => {});
          }
        })
        .catch((e) => setIndexError(e.message));
    }, 1000);
    return () => clearInterval(timer);
  }, [rebuilding, schemaUrl]);

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
    // A saved model changes what each step is set to run on, which the
    // Pipeline page states.
    getJson("/api/pipeline").then(setPipeline).catch(() => {});
  };

  // `source` is the database a guided question belongs to: clicking one from
  // another pile moves the schema panel to match, so what the rail shows is
  // the database the question is about.
  const ask = async (text, source) => {
    const q = (text === undefined ? question : text).trim();
    if (!q || busy || needsKey(meta, apiKeys)) return;
    if (source) setDatasource(source);
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
          // The key travels in a header, never in the body: a body is what
          // request logs and validation errors quote back.
          headers: askHeaders(apiKeys, stepModels),
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

  const saveKey = (provider, next) => setApiKeys(writeKeyFor(window.sessionStorage, provider, next));
  const saveStepModel = (agent, choice) =>
    setStepModels((models) => writeModel(window.sessionStorage, models, agent, choice));

  const sub = result && result.sub_queries && result.sub_queries[0];
  const used = planTables(sub && sub.plan);
  const denied = deniedTables(result && result.errors);
  const replay = meta && meta.mode === "replay";
  const hosted = Boolean(meta && meta.hosted);
  const canSet = settings && settings.available;
  const indexBroken = index && needsRebuild(index.health) && index.job.state !== "running";
  // Every registered database, and the one a run was answered from. With a
  // single database neither is a fact worth printing: the rail's heading
  // already names it and there is nothing for the resolver to choose.
  const databases = datasourceNames(meta);
  const answered = databases.length > 1 ? answeredDatasources(result) : [];
  const onAsk = page === "ask";
  // Hosted, with no key in this tab: the page says so and holds the question
  // box and the guided questions closed instead of letting a click fail.
  const noKey = needsKey(meta, apiKeys);
  // Empty until this tab has a key: the first-run state is saying it already.
  const modeNote = hosted ? hostedNote(meta, apiKeys) : "";
  const groups = guidedGroups(meta);
  const current = pageFor(page);
  const nav = navItems(page, {
    // Hosted, Settings is not off: it is where the visitor's own key goes.
    settings: settings && !settings.available && !settings.hosted,
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
              <strong>{hosted ? "Hosted demo." : replay ? "Replay mode." : "Live mode."}</strong>{" "}
              {hosted ? (
                modeNote && (
                  <>
                    {modeNote} <a href={hashFor("settings")}>Settings</a>.
                  </>
                )
              ) : replay ? (
                replayNote(meta.recorded_questions, (meta.questions || []).length, canSet)
              ) : (
                "Questions go to the configured model."
              )}
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
          <p className="page-lede">
            {(hosted && current.hostedDescription) || current.description}
          </p>
          {current.note && <p className="page-note">{current.note}</p>}
        </div>

        {onAsk && (
          <div className="layout">
            <section className="composer" aria-labelledby="ask-heading">
              <h2 id="ask-heading" className="visually-hidden">Ask</h2>
              {noKey && (
                <div className="first-run" id="first-run">
                  <h3>Add your API key to ask a question</h3>
                  <p id="first-run-why">
                    {/* The promise the code keeps; `hostedKey.js` is where it is kept. */}
                    This demo runs on your own API key. It stays in this browser tab, travels with each
                    question and is never stored on the server. The sample databases and their search
                    index are already built, so the key is the only thing missing.
                  </p>
                  <a className="first-run-go" href={hashFor("settings")}>Add your key</a>
                </div>
              )}
              {/* The page title above already says Ask. With one database this
                  names it; with three the resolver picks, so it must not. */}
              <label className="question-label" htmlFor="question">
                {groups.length > 1
                  ? "Your question"
                  : `Your question for the ${meta ? meta.dataset : "demo"} database`}
              </label>
              <textarea
                id="question"
                rows={2}
                value={question}
                placeholder="Which genre sells the most tracks?"
                disabled={noKey}
                title={noKey ? NO_KEY_REASON : undefined}
                aria-describedby={noKey ? "first-run-why" : undefined}
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
                <button className="ask" onClick={() => ask()} disabled={busy || noKey || !question.trim()}
                  title={noKey ? NO_KEY_REASON : undefined}
                  aria-describedby={noKey ? "first-run-why" : undefined}>
                  {busy ? "Asking" : "Ask"}
                  <kbd aria-hidden="true">Ctrl Enter</kbd>
                </button>
              </div>
              {groups.length > 0 && (
                <section className="guided" aria-labelledby="guided-heading">
                  <h3 id="guided-heading">Or try a guided question</h3>
                  {/* One database needs no labels; three do. */}
                  {groups.map((group) => (
                    <div className="guided-group" key={group.datasource}>
                      {groups.length > 1 && (
                        <h4 className="guided-source mono" id={`guided-${group.datasource}`}>
                          {group.datasource}
                        </h4>
                      )}
                      <ul aria-labelledby={groups.length > 1 ? `guided-${group.datasource}` : undefined}>
                        {group.questions.map((q) => (
                          <li key={q}>
                            <button className="guided-q" onClick={() => ask(q, group.datasource)}
                              disabled={busy || noKey}
                              title={noKey ? NO_KEY_REASON : undefined}
                              aria-describedby={noKey ? "first-run-why" : undefined}>{q}</button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </section>
              )}
            </section>

            <aside className="rail">
              <IndexPanel index={index} error={indexError} onRebuild={rebuildIndex} hosted={hosted} />
              <SchemaPanel schema={schema} used={used} denied={denied} role={asked && asked.role}
                datasources={databases} onDatasource={setDatasource} />
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
                answered={answered}
              />
            </section>
          </div>
        )}

        {page === "pipeline" && (
          <div className="sheet" id="pipeline-panel">
            <Pipeline pipeline={pipeline} error={pipelineError} result={result} asked={asked} />
          </div>
        )}

        {page === "settings" && (
          <div className="sheet" id="settings-panel">
            <Settings settings={settings} error={settingsError} onSaved={settingsSaved}
              recorded={meta ? meta.recorded_questions : 0}
              apiKeys={apiKeys} onKey={saveKey} limits={meta && meta.limits}
              stepModels={stepModels} onStepModel={saveStepModel} />
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
