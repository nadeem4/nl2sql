import React, { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import Settings from "./pages/Settings.jsx";
import Pipeline from "./pages/Pipeline.jsx";
import RetrievalInspector from "./pages/Retrieval.jsx";
import Home from "./pages/Home.jsx";
import { sampleQuestion } from "./pages/home.js";
import { listApprovals } from "./api/chat.js";
import { getMeta } from "./api/meta.js";
import { callerOf } from "./lib/caller.js";
import { toDecide } from "./chat/approval.js";
import { needsKey } from "./pages/firstRun.js";
import { modeStatus } from "./pages/status.js";
import { readKeys, writeKeyFor } from "./lib/hostedKey.js";
import { readModels, writeModel } from "./lib/hostedModels.js";
import {
  createRouter, hashFor, indicatorTransform, navItems, pageFor, routeFromHash, swapView,
} from "./router.js";

// Wave D's pages, each its own chunk. Chat and Approvals are real (W1); the others are stubs until their rows fill them
// (wave_d.md, section 14).
const Chat = lazy(() => import("./pages/Chat.jsx"));
const Approvals = lazy(() => import("./pages/Approvals.jsx"));

const STUB_PAGES = {
  dashboards: lazy(() => import("./pages/Dashboards.jsx")),
  connect: lazy(() => import("./pages/Connect.jsx")),
  knowledge: lazy(() => import("./pages/Knowledge.jsx")),
  evals: lazy(() => import("./pages/Evals.jsx")),
  trace: lazy(() => import("./pages/Trace.jsx")),
};

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
  const [route, setRoute] = useState(() => routeFromHash(window.location.hash));
  useEffect(() => {
    const router = createRouter(window);
    // The hash can change between the first render and this effect.
    setRoute(router.route());
    const drop = router.subscribe((next) =>
      swapView(document, () => flushSync(() => setRoute(next)), { reduce: reducedMotion() }));
    return () => {
      drop();
      router.stop();
    };
  }, []);
  return route;
}

// One underline for the whole nav, slid under the current tab (styles/ask-run.css,
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
  const { page, param } = useRoute();
  const { navRef, indRef } = useNavIndicator(page);
  const [meta, setMeta] = useState(null);
  const [role, setRole] = useState("admin");
  const [error, setError] = useState(null);
  const [settings, setSettings] = useState(null);
  const [settingsError, setSettingsError] = useState(null);
  const [retrieval, setRetrieval] = useState(null);
  const [retrievalError, setRetrievalError] = useState(null);
  const [pipeline, setPipeline] = useState(null);
  const [pipelineError, setPipelineError] = useState(null);
  // A question another page wants Chat to ask (the sample on Home), and how many approvals wait for a decision.
  const [starter, setStarter] = useState(null);
  const [waiting, setWaiting] = useState([]);
  // Who the caller is, from GET /meta (A4c): Connect and Knowledge offer admin work only to an admin. Null until /meta answers, or when it names no caller.
  const [caller, setCaller] = useState(null);
  // The hosted demo keys and step choices: this tab, never the server.
  const [apiKeys, setApiKeys] = useState(() => readKeys(window.sessionStorage));
  const [stepModels, setStepModels] = useState(() => readModels(window.sessionStorage));
  const pageRef = useRef(null);
  const firstPage = useRef(true);

  useEffect(() => {
    getJson("/api/meta")
      .then((m) => {
        setMeta(m);
        if (m.roles.length) setRole(m.roles.includes("admin") ? "admin" : m.roles[0]);
      })
      .catch((e) => setError(e.message));
    getMeta().then((m) => setCaller(callerOf(m))).catch(() => {});
    getJson("/api/settings").then(setSettings).catch((e) => setSettingsError(e.message));
    getJson("/api/retrieval").then(setRetrieval).catch((e) => setRetrievalError(e.message));
    getJson("/api/pipeline").then(setPipeline).catch((e) => setPipelineError(e.message));
  }, []);

  // The Approvals tab shows how many requests wait; it is read again on each page change, which is when a decision
  // or a new request is most likely to have happened. A caller who may not see approvals simply sees no count.
  useEffect(() => {
    listApprovals("pending").then(setWaiting).catch(() => setWaiting([]));
  }, [page]);

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

  // A saved key can turn replay into live; the status pill follows the server.
  const settingsSaved = (next) => {
    setSettings(next);
    setMeta((m) => (m ? { ...m, mode: next.mode } : m));
    // A saved model changes what each step is set to run on, which the
    // Pipeline page states.
    getJson("/api/pipeline").then(setPipeline).catch(() => {});
  };

  // Try a sample question on Home: open Chat and have it ask one, a recorded one when the server has some.
  const trySample = () => {
    const q = sampleQuestion(meta);
    window.location.hash = hashFor("chat");
    if (q) setStarter(q);
  };

  const saveKey = (provider, next) => setApiKeys(writeKeyFor(window.sessionStorage, provider, next));
  const saveStepModel = (agent, choice) =>
    setStepModels((models) => writeModel(window.sessionStorage, models, agent, choice));

  const replay = meta && meta.mode === "replay";
  const hosted = Boolean(meta && meta.hosted);
  const canSet = settings && settings.available;
  const noKey = needsKey(meta, apiKeys);
  const status = modeStatus(meta, apiKeys, { canSet });
  const current = pageFor(page);
  const nav = navItems(page, {
    // Hosted, Settings is not off: it is where the visitor puts their own key.
    settings: settings && !settings.available && !settings.hosted,
    retrieval: retrieval && !retrieval.available,
  }, { approvals: toDecide(waiting).length });

  return (
    <div className="app">
      {/* A button, not a fragment link: the hash belongs to the router. */}
      <button className="skip" onClick={() => focusById("page")}>Skip to the page</button>

      <header className="topbar">
        {/* The product mark, not the page heading: the page title is. */}
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
                  {item.badge && <span className="nav-badge" aria-label={`${item.badge} waiting`}>{item.badge}</span>}
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

      <main className="page" id="page" ref={pageRef} tabIndex={-1}
        aria-labelledby={page === "home" ? "home-title" : "page-title"}>
        {error && <p className="fault" role="alert">{error}</p>}
        {page === "home" && <Home meta={meta} onTry={trySample} />}
        {/* On Chat the conversation says where you are; the heading stays for screen readers. Home has its own. */}
        {page !== "home" && <div className={page === "chat" ? "page-head visually-hidden" : "page-head"}>
          <h1 id="page-title">{current.title}</h1>
          <p className="page-lede">
            {(hosted && current.hostedDescription) || current.description}
          </p>
          {current.note && <p className="page-note">{current.note}</p>}
        </div>}

        {page === "chat" && (
          <Suspense fallback={null}>
            <Chat param={param} starter={starter} onStarter={() => setStarter(null)} />
          </Suspense>
        )}

        {page === "approvals" && (
          <Suspense fallback={null}>
            <Approvals />
          </Suspense>
        )}

        {page === "pipeline" && (
          <div className="sheet" id="pipeline-panel">
            <Pipeline pipeline={pipeline} error={pipelineError} result={null} asked={null}
              replaying={replay || noKey} />
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
            <RetrievalInspector options={retrieval} error={retrievalError} question="" role={role} />
          </div>
        )}

        {STUB_PAGES[page] && (
          <Suspense fallback={null}>
            {React.createElement(STUB_PAGES[page], { param, caller })}
          </Suspense>
        )}
      </main>
    </div>
  );
}
