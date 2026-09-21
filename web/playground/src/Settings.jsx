import React, { useEffect, useState } from "react";
import { changedModels, choicesFrom, modelOptions, variableModels } from "./settings.js";

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

function KeyForm({ settings, onSaved }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [fault, setFault] = useState(null);
  const current = settings.key && settings.key.masked;

  const save = async (e) => {
    e.preventDefault();
    if (!key.trim() || busy) return;
    setBusy(true);
    setFault(null);
    setStatus(null);
    try {
      const next = await send("/api/settings/key", { api_key: key });
      setKey("");
      setStatus(`Saved. Questions now go to ${next.provider === "openrouter" ? "OpenRouter" : "OpenAI"}.`);
      onSaved(next);
    } catch (err) {
      setFault(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="settings-block" onSubmit={save} aria-labelledby="settings-key-heading">
      <h3 id="settings-key-heading">API key</h3>
      <p className="settings-current" id="settings-key-current">
        {current ? (
          <>In use: <code>{current}</code> from <code>{settings.key.env_var}</code></>
        ) : (
          "No key in use. Guided questions answer from recordings."
        )}
      </p>
      <label className="settings-label" htmlFor="settings-key">
        {current ? "Replace it with" : "Paste a key"}
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
        A key starting <code>sk-or-</code> is OpenRouter; any other is OpenAI. It is written to{" "}
        <code>{settings.files.env}</code> and the demo switches to live without a restart. On a
        later start, <code>--api-key</code> or a key exported in your shell still wins. The key is
        never shown again, only its last four characters.
      </p>
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
    </form>
  );
}

function ModelsForm({ settings, onSaved }) {
  const [choices, setChoices] = useState(() => choicesFrom(settings.nodes));
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [fault, setFault] = useState(null);
  const hasList = settings.models.length > 0;
  const options = modelOptions(settings.models, settings.default_model);
  const changes = changedModels(settings.nodes, choices);
  const loose = variableModels(settings.models, choices);

  useEffect(() => setChoices(choicesFrom(settings.nodes)), [settings.nodes]);

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
      <h3 id="settings-models-heading">Model for each step</h3>
      {!hasList && <p className="notice">{settings.models_note}</p>}
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
              onChange={(e) => setChoices({ ...choices, [node.agent]: e.target.value })}
            >
              {options.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
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
            Written to <code>{settings.files.llm}</code>, the file the CLI reads. Each model here
            worked with the engine's parameters in a check on 2026-09-20; whether it plans well is for
            the evaluation to show.
          </p>
        </div>
      )}
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
    </form>
  );
}

// The settings panel: a secondary surface, closed until asked for. When the
// server has settings off it says why instead of offering a form that fails.
export default function Settings({ settings, error, onSaved }) {
  if (error) {
    return <p className="fault">Settings could not be loaded: {error}</p>;
  }
  if (!settings) {
    return <p className="settings-help">Loading settings</p>;
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
      <KeyForm settings={settings} onSaved={onSaved} />
      <ModelsForm settings={settings} onSaved={onSaved} />
    </div>
  );
}
