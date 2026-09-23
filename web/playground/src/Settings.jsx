import React, { useEffect, useState } from "react";
import { changedModels, choicesFrom, modelGroups, variableModels } from "./settings.js";
import { looksLikeKey, maskKey } from "./hostedKey.js";

// Sends JSON and returns the parsed reply; a refusal carries the server's own
// sentence in `detail`, which is what the panel shows.
async function send(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const reply = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof reply.detail === "string" ? reply.detail : `The server answered ${response.status}.`;
    throw new Error(detail);
  }
  return reply;
}

const PROVIDER_NAMES = { openai: "OpenAI", openrouter: "OpenRouter", anthropic: "Anthropic" };

function KeyForm({ settings, onSaved, recorded }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [fault, setFault] = useState(null);
  const current = settings.key && settings.key.masked;
  // One key per provider; the default's may be one without a model list.
  const saved = (settings.providers || []).filter((p) => p.masked);
  if (current && !saved.some((p) => p.env_var === settings.key.env_var)) {
    saved.unshift({ id: settings.provider, label: PROVIDER_NAMES[settings.provider] || settings.provider,
                    masked: current, env_var: settings.key.env_var });
  }

  const save = async (e) => {
    e.preventDefault();
    if (!key.trim() || busy) return;
    setBusy(true);
    setFault(null);
    setStatus(null);
    try {
      const next = await send("/api/settings/key", { api_key: key });
      setKey("");
      setStatus(`Saved. Questions now go to ${PROVIDER_NAMES[next.provider] || "OpenAI"}.`);
      onSaved(next);
    } catch (err) {
      setFault(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="settings-block" onSubmit={save} aria-labelledby="settings-key-heading">
      <h3 id="settings-key-heading">API keys</h3>
      {saved.length ? (
        <ul className="settings-current settings-keys" id="settings-key-current">
          {saved.map((p) => (
            <li key={p.env_var}>
              {p.label}: <code>{p.masked}</code> from <code>{p.env_var}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p className="settings-current" id="settings-key-current">
          {recorded
            ? `No key in use. ${recorded} guided ${recorded === 1 ? "question answers" : "questions answer"} from recordings.`
            : "No key in use, and there are no recorded answers. Paste a key to ask questions."}
        </p>
      )}
      <label className="settings-label" htmlFor="settings-key">
        {saved.length ? "Add or replace a key" : "Paste a key"}
      </label>
      <div className="settings-row">
        <input
          id="settings-key"
          type="password"
          value={key}
          autoComplete="off"
          spellCheck={false}
          aria-describedby="settings-key-help"
          onChange={(e) => setKey(e.target.value)}
        />
        <button id="settings-save-key" className="settings-save" type="submit" disabled={busy || !key.trim()}>
          {busy ? "Saving" : "Save key"}
        </button>
      </div>
      <p className="settings-help" id="settings-key-help">
        A key starting <code>sk-ant-</code> is Anthropic, <code>sk-or-</code> is OpenRouter; any
        other is OpenAI. Each provider keeps one key, written to{" "}
        <code>{settings.files.env}</code>, and the default moves to the provider of the key you
        save, without a restart. Steps put on another provider stay there. On a later start,{" "}
        <code>--api-key</code> or a key exported in your shell still wins. A key is never shown
        again, only its last four characters.
      </p>
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
    </form>
  );
}

function ModelsForm({ settings, onSaved }) {
  const providers = settings.providers || [];
  const [choices, setChoices] = useState(() => choicesFrom(settings.nodes, settings.provider));
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [fault, setFault] = useState(null);
  const hasList = providers.some((p) => p.usable && p.models.length > 0);
  const groups = modelGroups(providers, settings.default_model);
  const changes = changedModels(settings.nodes, choices, settings.provider);
  const loose = variableModels(providers, choices);

  useEffect(() => setChoices(choicesFrom(settings.nodes, settings.provider)), [settings.nodes, settings.provider]);

  const save = async (e) => {
    e.preventDefault();
    if (!Object.keys(changes).length || busy) return;
    setBusy(true);
    setFault(null);
    setStatus(null);
    try {
      const next = await send("/api/settings/models", { models: changes });
      setStatus("Saved. The next question uses these models.");
      onSaved(next);
    } catch (err) {
      setFault(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="settings-block" onSubmit={save} aria-labelledby="settings-models-heading">
      <h3 id="settings-models-heading">Provider and model for each step</h3>
      {settings.models_note && <p className="notice">{settings.models_note}</p>}
      <ul className="model-rows">
        {settings.nodes.map((node) => (
          <li key={node.agent}>
            <label htmlFor={`model-${node.agent}`}>
              <span className="model-step">{node.label}</span>
              <span className="model-does">{node.does}</span>
            </label>
            <select
              id={`model-${node.agent}`}
              value={choices[node.agent] || ""}
              disabled={!hasList}
              aria-invalid={node.unavailable ? true : undefined}
              aria-describedby={node.unavailable ? `model-${node.agent}-unavailable` : undefined}
              onChange={(e) => setChoices({ ...choices, [node.agent]: e.target.value })}
            >
              {groups.map((g) =>
                g.label === null ? (
                  g.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)
                ) : (
                  <optgroup key={g.label} label={g.label} disabled={g.disabled}>
                    {g.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </optgroup>
                ),
              )}
            </select>
            {node.unavailable && (
              <p className="model-unavailable" id={`model-${node.agent}-unavailable`}>{node.unavailable}</p>
            )}
          </li>
        ))}
      </ul>
      {loose.length > 0 && (
        <p className="settings-warn" id="settings-temperature-note">
          <code>{loose.join(", ")}</code> {loose.length === 1 ? "does" : "do"} not accept temperature 0,
          so {loose.length === 1 ? "it runs" : "they run"} at the model's default temperature. A step on{" "}
          {loose.length === 1 ? "it" : "one of them"} gives answers that vary more from run to run.
        </p>
      )}
      {hasList && (
        <div className="settings-row settings-foot">
          <button id="settings-save-models" className="settings-save" type="submit"
                  disabled={busy || !Object.keys(changes).length}>
            {busy ? "Saving" : "Save models"}
          </button>
          <p className="settings-help">
            Written to <code>{settings.files.llm}</code>, the file the CLI reads. The OpenAI models
            worked with the engine's parameters in a check on 2026-09-20; the Claude ones follow
            Anthropic's documented parameter rules. Whether a model plans well is for the evaluation
            to show.
          </p>
        </div>
      )}
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
    </form>
  );
}

// The hosted demo's key form. Nothing here talks to the server: the key is
// put into this tab's storage and sent as a header with each question. The
// wording is the promise the code keeps, so it says exactly what happens and
// where to go for anything more.
function HostedKeyForm({ apiKey, onKey, limits, reason }) {
  const [key, setKey] = useState("");
  const [fault, setFault] = useState(null);
  const [status, setStatus] = useState(null);

  const save = (e) => {
    e.preventDefault();
    if (!key.trim()) return;
    if (!looksLikeKey(key)) {
      setFault("That does not look like an API key: expected 20 or more letters, digits, '-' or '_', with no spaces.");
      return;
    }
    setFault(null);
    onKey(key);
    setKey("");
    setStatus("Kept in this browser tab. Ask a question and it goes with it.");
  };

  const forget = () => {
    onKey("");
    setStatus("Cleared from this tab.");
  };

  return (
    <form className="settings-block" onSubmit={save} aria-labelledby="hosted-key-heading">
      <h3 id="hosted-key-heading">Your API key</h3>
      <p className="settings-current" id="hosted-key-current">
        {apiKey ? (
          <>In this tab: <code>{maskKey(apiKey)}</code>.</>
        ) : (
          "No key in this tab yet, so questions cannot be answered."
        )}
      </p>
      <label className="settings-label" htmlFor="hosted-key">
        {apiKey ? "Replace the key" : "Paste an OpenAI, Anthropic or OpenRouter key"}
      </label>
      <div className="settings-row">
        <input
          id="hosted-key"
          type="password"
          value={key}
          autoComplete="off"
          spellCheck={false}
          aria-describedby="hosted-key-help"
          onChange={(e) => setKey(e.target.value)}
        />
        <button id="hosted-save-key" className="settings-save" type="submit" disabled={!key.trim()}>
          Use this key
        </button>
        {apiKey && (
          <button type="button" className="linkish" onClick={forget}>Clear it</button>
        )}
      </div>
      <p className="settings-help" id="hosted-key-help">
        {reason} It is kept in this tab's <code>sessionStorage</code>, sent as a request header with
        each question, used to call the model for that one question and then dropped: it is never
        written to a file, an environment variable, a log or a run trace on the server, and no
        other visitor can reach it. Closing the tab clears it. The demo answers only from its own
        three sample databases
        {limits && limits.questions_per_minute
          ? `, up to ${limits.questions_per_minute} questions a minute and ${limits.questions_per_session} a session`
          : ""}
        . To ask questions of your own data, with no limits and a key that stays on your machine,
        run it locally:{" "}
        <code>pip install "nl2sql-engine[demo]"</code> then <code>nl2sql demo</code>.
      </p>
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
    </form>
  );
}

// The settings panel: a secondary surface, closed until asked for. When the
// server has settings off it says why instead of offering a form that fails.
export default function Settings({ settings, error, onSaved, recorded, apiKey, onKey, limits }) {
  if (error) {
    return <p className="fault">Settings could not be loaded: {error}</p>;
  }
  if (!settings) {
    return <p className="settings-help">Loading settings</p>;
  }
  if (settings.hosted) {
    // Not "off": there is nothing for the server to save, and the one thing
    // the visitor does need to set lives in their own browser.
    return (
      <div className="settings-grid">
        <HostedKeyForm apiKey={apiKey} onKey={onKey} limits={limits} reason={settings.reason} />
      </div>
    );
  }
  if (!settings.available) {
    return (
      <div className="settings-off" id="settings-unavailable">
        <h3>Settings are off</h3>
        <p>{settings.reason}</p>
      </div>
    );
  }
  return (
    <div className="settings-grid">
      <KeyForm settings={settings} onSaved={onSaved} recorded={recorded} />
      <ModelsForm settings={settings} onSaved={onSaved} />
    </div>
  );
}
