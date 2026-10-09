import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import SchemaPanel from "./SchemaPanel.jsx";
import IndexPanel from "./IndexPanel.jsx";
import { needsRebuild } from "./indexHealth.js";
import Run from "./Panes.jsx";
import Settings from "./Settings.jsx";
import Pipeline from "./Pipeline.jsx";
import RetrievalInspector from "./Retrieval.jsx";
import { answeredDatasources, datasourceNames } from "./datasources.js";
import { guidedGroups } from "./questions.js";
import { needsKey } from "./firstRun.js";
import { AskDock, Elapsed, FirstRun, Suggestions } from "./AskParts.jsx";
import { STOP_AFTER_MS, askButton, runScroll } from "./runState.js";
import { modeStatus } from "./status.js";
import { askHeaders, readKeys, writeKeyFor } from "./hostedKey.js";
import { readModels, writeModel } from "./hostedModels.js";
import {
  createRouter, hashFor, indicatorTransform, navItems, pageFor, pageFromHash, swapView,
} from "./router.js";
import { deniedTables, planTables } from "./run.js";

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

const reducedMotion = () =>
  Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

// The address bar says which page is open, so a reload and a shared link both
// work and Back walks the pages you visited. A change of page crossfades in a
// view transition where the browser has one (router.js, `swapView`); flushSync
// puts the new page in the DOM inside the transition's callback.
function useRoute() {
  const [page, setPage] = useState(() => pageFromHash(window.location.hash));
  useEffect(() => {
    const router = createRouter(window);
    // The hash can change between the first render and this effect.
    setPage(router.page());
    const drop = router.subscribe((next) =>
      swapView(document, () => flushSync(() => setPage(next)), { reduce: reducedMotion() }));
    return () => {
      drop();
      router.stop();
    };
  }, []);
  return page;
}

// One underline for the whole nav, slid under the current tab (styles.css,
// `.nav-ind`). It is placed without a transition first, then `data-ind="on"`
// lets later moves slide; until then the current tab's own border shows.
function useNavIndicator(page) {
  const navRef = useRef(null);
  const indRef = useRef(null);
  useLayoutEffect(() => {
    const nav = navRef.current;
    const ind = indRef.current;
    if (!nav || !ind) return undefined;
    const place = () => {
      const link = nav.querySelector('.nav-link[aria-current="page"]');
      const transform = indicatorTransform(link && link.getBoundingClientRect(), nav.getBoundingClientRect());
      if (!transform) return;
      ind.style.transform = transform;
      if (!nav.dataset.ind) requestAnimationFrame(() => { nav.dataset.ind = "on"; });
    };
    place();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(place) : null;
    if (observer) observer.observe(nav);
    window.addEventListener("resize", place);
    return () => {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [page]);
  return { navRef, indRef };
}

function focusById(id) {
  const target = document.getElementById(id);
  if (!target) return;
  // A control inside a shut <details> (the rail's search index) cannot take
  // focus until it is opened.
  for (let fold = target.closest("details"); fold; fold = fold.parentElement && fold.parentElement.closest("details")) {
    fold.open = true;
  }
  target.focus();
}

export default function App() {
  const page = useRoute();
  const { navRef, indRef } = useNavIndicator(page);
  const [meta, setMeta] = useState(null);
  const [schema, setSchema] = useState(null);
  const [question, setQuestion] = useState("");
  const [role, setRole] = useState("admin");
  const [planOnly, setPlanOnly] = useState(false);
  const [debug, setDebug] = useState(readDebug);
  const [result, setResult] = useState(null);
  const [asked, setAsked] = useState(null);
  const [busy, setBusy] = useState(false);
  // When the run in flight started, whether it may be stopped yet, and whether
  // the visitor stopped the last one.
  const [startedAt, setStartedAt] = useState(null);
  const [stoppable, setStoppable] = useState(false);
  const [stopped, setStopped] = useState(false);
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
  const abortRef = useRef(null);
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
    // Instant, whatever `scroll-behavior` says: the new page fades in at its top.
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
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

  // A saved key can turn replay into live; the status pill follows the server.
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
    if (busy) return;
    // An empty box looks ready rather than grey; pressing Ask then puts the
    // keyboard where the question goes.
    if (!q) {
      focusById("question");
      return;
    }
    if (source) setDatasource(source);
    setQuestion(q);
    setAsked({ question: q, role, planOnly });
    setBusy(true);
    setStopped(false);
    setError(null);
    setResult(null);
    const controller = new AbortController();
    abortRef.current = controller;
    setStartedAt(performance.now());
    setStoppable(false);
    const stopTimer = setTimeout(() => setStoppable(true), STOP_AFTER_MS);
    // On a single-column layout the run sits below the fold; bring it up,
    // only as far as it must come.
    // The target is the answer header, not the whole run: "nearest" leaves an
    // element taller than the screen where it is.
    const head = document.getElementById("pane-question") || runRef.current;
    const scroll = head && runScroll(head.getBoundingClientRect(), window.innerHeight, { reduce: reducedMotion() });
    if (scroll) head.scrollIntoView(scroll);
    try {
      setResult(
        await getJson("/api/ask", {
          method: "POST",
          // The key travels in a header, never in the body: a body is what
          // request logs and validation errors quote back.
          headers: askHeaders(apiKeys, stepModels),
          body: JSON.stringify({ question: q, role, execute: !planOnly }),
          signal: controller.signal,
        })
      );
    } catch (e) {
      if (e.name === "AbortError") setStopped(true);
      else setError(e.message);
    } finally {
      clearTimeout(stopTimer);
      abortRef.current = null;
      setBusy(false);
      setStoppable(false);
    }
  };

  // Stop aborts the request; the server sees it drop and cancels the run.
  const stop = () => {
    if (abortRef.current) abortRef.current.abort();
  };

  const toggleDebug = (on) => {
    setDebug(on);
    writeDebug(on);
  };

  const saveKey = (provider, next) => setApiKeys(writeKeyFor(window.sessionStorage, provider, next));
  // The first-run form: once the key is in, the question box is the next step.
  const firstKey = (provider, next) => {
    saveKey(provider, next);
    setTimeout(() => focusById("question"), 0);
  };
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
  // Hosted, with no key in this tab: the page says what a keyless visitor can
  // do -- replay the recorded guided questions -- and leads with them. Nothing
  // is held closed; a question without a recording answers with the way to
  // add a key.
  const noKey = needsKey(meta, apiKeys);
  const status = modeStatus(meta, apiKeys, { canSet });
  const groups = guidedGroups(meta);
  const button = askButton({ busy, elapsedMs: stoppable ? STOP_AFTER_MS : 0 });
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
        {/* The product mark, not the page's heading: the page title is. */}
        <p className="wordmark">
          <span className="mono">nl2sql</span> <span className="wordmark-sub">playground</span>
        </p>
        <nav className="nav" aria-label="Playground pages" ref={navRef}>
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
          <span className="nav-ind" ref={indRef} aria-hidden="true" />
        </nav>
        {/* The mode in a few words; the sentence it stands for is the
            tooltip, and is read out in full. */}
        {status && (
          <p className={`status mode-${meta.mode}`} data-tone={status.tone} title={status.detail}>
            <span className="pulse" aria-hidden="true" />
            <span className="status-label">{status.label}</span>
            <span className="visually-hidden">. {status.detail}</span>
          </p>
        )}
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
        {/* On Ask the open tab already says where you are; the heading stays
            for the page's label and for screen readers. */}
        <div className={onAsk ? "page-head visually-hidden" : "page-head"}>
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
              {noKey && <FirstRun databases={databases} meta={meta} onKey={firstKey} />}
              {noKey && (
                <Suggestions groups={groups} selected={datasource} ran={Boolean(asked)} busy={busy}
                  noKey meta={meta} onAsk={ask} />
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
                {busy && startedAt !== null && <Elapsed since={startedAt} />}
                {/* Keeps its fill while busy; after a moment it becomes Stop. */}
                <button className="ask" id="ask" data-mode={button.action} aria-busy={busy || undefined}
                  onClick={() => (button.action === "stop" ? stop() : button.action === "ask" ? ask() : null)}
                  >
                  {button.action === "wait" && <span className="spin" aria-hidden="true" />}
                  {button.action === "stop" && (
                    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                      <rect x="2" y="2" width="8" height="8" rx="1.5" fill="currentColor" />
                    </svg>
                  )}
                  {button.label}
                  {button.action === "ask" && <kbd aria-hidden="true">Ctrl Enter</kbd>}
                </button>
              </div>
              {!noKey && (
                <Suggestions groups={groups} selected={datasource} ran={Boolean(asked)} busy={busy}
                  meta={meta} onAsk={ask} />
              )}
            </section>

            <aside className="rail">
              <SchemaPanel schema={schema} used={used} denied={denied} role={asked && asked.role}
                datasources={databases} onDatasource={setDatasource} />
              <IndexPanel index={index} error={indexError} onRebuild={rebuildIndex} hosted={hosted} />
            </aside>

            <section className="run" id="run" ref={runRef} aria-labelledby="run-heading" tabIndex={-1}>
              <h2 id="run-heading" className="visually-hidden">The run</h2>
              <Run
                asked={asked}
                result={result}
                sub={sub}
                busy={busy}
                stopped={stopped}
                hosted={hosted}
                error={error}
                onAgain={asked ? () => ask(asked.question) : undefined}
                debug={debug}
                replay={replay}
                meta={meta}
                feedback={feedback}
                answered={answered}
              />
            </section>
            {!busy && (result || error || stopped) && <AskDock />}
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
