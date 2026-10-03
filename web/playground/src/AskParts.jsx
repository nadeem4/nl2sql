import React, { useEffect, useRef, useState } from "react";
import { KEY_PROVIDERS, looksLikeKey, providerForKey } from "./hostedKey.js";
import { NO_KEY_REASON } from "./firstRun.js";
import { providerName } from "./settings.js";
import { chipRow, elapsedLabel } from "./runState.js";

const PLACEHOLDER = { openai: "sk-...", anthropic: "sk-ant-...", openrouter: "sk-or-..." };
const COUNT = ["no", "one", "two", "three", "four", "five", "six"];

// The hosted demo before a key: the pitch, the key form and the three facts
// about where the key goes. Saving puts the key in this tab exactly as the
// Settings form does (`hostedKey.js`); the provider follows the key's own
// prefix once one is typed, because that is how the server reads it too.
export function FirstRun({ databases, onKey }) {
  const [chosen, setChosen] = useState("openai");
  const [key, setKey] = useState("");
  const [problem, setProblem] = useState(null);
  const provider = key.trim() ? providerForKey(key) : chosen;
  const n = databases.length;
  const pitch = n > 1
    ? `Ask ${COUNT[n] || n} real databases in plain English.`
    : "Ask a real database in plain English.";

  const save = (e) => {
    e.preventDefault();
    if (!key.trim()) return;
    if (!looksLikeKey(key)) {
      setProblem("That does not look like an API key: expected 20 or more letters, digits, '-' or '_', with no spaces.");
      return;
    }
    onKey(providerForKey(key), key);
  };

  return (
    <div className="first-run" id="first-run">
      <p className="first-run-kicker">Hosted demo</p>
      <h3>{pitch}</h3>
      <p id="first-run-why">
        {/* The promise the code keeps; `hostedKey.js` is where it is kept. */}
        This demo runs on your own API key. The model writes a typed plan, the checks review it, and only
        then is SQL written and run. The sample databases and their search index are already built, so the
        key is the only thing missing.
      </p>
      <form className="first-run-form" onSubmit={save} aria-label="Your API key">
        <fieldset className="first-run-providers">
          <legend>Provider</legend>
          {KEY_PROVIDERS.map((p) => (
            <label key={p} className="first-run-provider" data-on={p === provider || undefined}>
              <input type="radio" name="first-run-provider" id={`first-run-provider-${p}`} value={p}
                checked={p === provider} onChange={() => { setChosen(p); setKey(""); setProblem(null); }} />
              {providerName(p)}
            </label>
          ))}
        </fieldset>
        <label className="first-run-label" htmlFor="first-run-key">Your {providerName(provider)} key</label>
        <div className="first-run-row">
          <input id="first-run-key" type="password" value={key} autoComplete="off" spellCheck={false}
            placeholder={PLACEHOLDER[provider]} aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? "first-run-problem" : "first-run-facts"}
            onChange={(e) => { setKey(e.target.value); setProblem(null); }} />
          <button id="first-run-save" className="first-run-go" type="submit" disabled={!key.trim()}>
            Use this key
          </button>
        </div>
        {problem && <p className="first-run-problem" id="first-run-problem" role="alert">{problem}</p>}
      </form>
      <dl className="first-run-facts" id="first-run-facts">
        <div><dt>Stored</dt><dd>In this browser tab only.</dd></div>
        <div><dt>Sent</dt><dd>With each question, in a request header.</dd></div>
        <div><dt>Never</dt><dd>Written to disk, logs or traces.</dd></div>
      </dl>
      <p className="first-run-more">
        A key per provider and a model per step are under <a href="#/settings">Settings</a>.
      </p>
    </div>
  );
}

// The guided questions as a row of chips in the composer: a few from the
// selected database, and "N more" opening every pile. Every pile is always in
// the page (`#guided-<ds>`), hidden until opened.
export function Suggestions({ groups, selected, ran, busy, noKey, onAsk }) {
  const [open, setOpen] = useState(false);
  if (!groups.length) return null;
  const row = chipRow(groups, selected, { ran });
  const many = groups.length > 1;
  const lock = {
    disabled: busy || noKey,
    title: noKey ? NO_KEY_REASON : undefined,
    "aria-describedby": noKey ? "first-run-why" : undefined,
  };
  const pick = (question, datasource) => {
    setOpen(false);
    onAsk(question, datasource);
  };
  return (
    <section className="guided" aria-labelledby="guided-heading" data-ran={ran || undefined}>
      <div className="chips">
        <h3 id="guided-heading" className="guided-label">{noKey ? "Then try" : "Try"}</h3>
        <ul className="chip-list" aria-labelledby="guided-heading">
          {row.chips.map((q) => (
            <li key={q}>
              <button type="button" className="chip" onClick={() => pick(q, row.datasource)} {...lock}>{q}</button>
            </li>
          ))}
          {row.more > 0 && (
            <li>
              <button type="button" className="chip chip-more" aria-expanded={open} aria-controls="guided-all"
                onClick={() => setOpen(!open)}>
                {open ? "Fewer" : `${row.more} more`}
              </button>
            </li>
          )}
        </ul>
      </div>
      <div className="guided-all" id="guided-all" hidden={!open}>
        {groups.map((group) => (
          <div className="guided-group" key={group.datasource}>
            <h4 className={many ? "guided-source mono" : "visually-hidden"} id={`guided-${group.datasource}`}>
              {group.datasource}
            </h4>
            <ul aria-labelledby={`guided-${group.datasource}`}>
              {group.questions.map((q) => (
                <li key={q}>
                  <button type="button" className="guided-q" onClick={() => pick(q, group.datasource)} {...lock}>{q}</button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

// "Running · 3.4 s", drawn every frame; a screen reader hears it once a second.
export function Elapsed({ since }) {
  const shown = useRef(null);
  const [spoken, setSpoken] = useState(() => elapsedLabel(0, { spoken: true }));
  useEffect(() => {
    let frame;
    const tick = () => {
      const ms = performance.now() - since;
      if (shown.current) shown.current.textContent = elapsedLabel(ms);
      const say = elapsedLabel(ms, { spoken: true });
      setSpoken((before) => (before === say ? before : say));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [since]);
  return (
    <span className="elapsed" id="elapsed">
      <span ref={shown} aria-hidden="true">{elapsedLabel(0)}</span>
      <span className="visually-hidden" role="status">{spoken}</span>
    </span>
  );
}

// On a phone, once a run is on the page, the question box is far above it.
// This bar brings it back, and stays out of the way while the box is in view.
export function AskDock() {
  const [inView, setInView] = useState(true);
  useEffect(() => {
    const box = document.getElementById("question");
    if (!box || typeof IntersectionObserver === "undefined") return undefined;
    const watch = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting));
    watch.observe(box);
    return () => watch.disconnect();
  }, []);
  const go = () => {
    const box = document.getElementById("question");
    if (!box) return;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    box.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    box.focus({ preventScroll: true });
  };
  return (
    <div className="ask-dock" hidden={inView}>
      <button type="button" className="ask-dock-btn" onClick={go}>
        <span className="ask-dock-ph">Ask another question</span>
        <span className="ask-dock-go" aria-hidden="true">Ask</span>
      </button>
    </div>
  );
}
