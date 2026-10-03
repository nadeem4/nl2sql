import React, { useEffect, useState } from "react";
import { PROVIDER_NAMES, changedModels, choicesFrom, modelGroups, variableModels } from "./settings.js";
import { keyMismatch, keyTail, looksLikeKey, providerCards, providerForKey } from "./hostedKey.js";
import { providersNeeded } from "./hostedModels.js";

// Settings: the key and the model each step runs on, as two raised cards side
// by side on a wide window. Each card says what it holds in one line, and how
// a key is handled in three facts; the full sentence on that sits behind a
// disclosure, because it is a promise to be able to check, not to read first.

// Wide enough for the two cards to sit side by side, so the models card can
// start open without pushing the key card off the screen.
const WIDE = "(min-width: 1060px)";
const wide = () => typeof window !== "undefined" && window.matchMedia && window.matchMedia(WIDE).matches;

const PLACEHOLDERS = { openai: "sk-…", anthropic: "sk-ant-…", openrouter: "sk-or-…" };

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

// Stored / Sent / Never: the three things to know about a key, as the code
// keeps them.
function Facts({ id, facts }) {
  return (
    <dl className="facts" id={id}>
      {facts.map(([term, text]) => (
        <div className="fact" key={term}><dt>{term}</dt><dd>{text}</dd></div>
      ))}
    </dl>
  );
}

function ModelSelect({ id, value, groups, disabled, invalid, describedBy, onChange }) {
  return (
    <select id={id} value={value} disabled={disabled} aria-invalid={invalid ? true : undefined}
            aria-describedby={describedBy} onChange={(e) => onChange(e.target.value)}>
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
  );
}

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
    <form className="set-card" onSubmit={save} aria-labelledby="settings-key-heading">
      <h2 id="settings-key-heading">API keys</h2>
      {saved.length ? (
        <ul className="saved-keys" id="settings-key-current">
          {saved.map((p) => (
            <li className="saved" key={p.env_var}>
              <span className="pulse" aria-hidden="true" />
              <span>
                {p.label} key ending <span className="mono">…{String(p.masked).slice(-4)}</span>, from{" "}
                <code>{p.env_var}</code>.
              </span>
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
      <div className="key-field">
        <label className="settings-label" htmlFor="settings-key">
          {saved.length ? "Add or replace a key" : "Paste a key"}
        </label>
        <div className="key-row">
          <input
            id="settings-key"
            className="key-input"
            type="password"
            value={key}
            placeholder="sk-…"
            autoComplete="off"
            spellCheck={false}
            aria-describedby="settings-key-facts settings-key-help"
            onChange={(e) => setKey(e.target.value)}
          />
          <button id="settings-save-key" className="settings-save" type="submit" disabled={busy || !key.trim()}>
            {busy ? "Saving" : "Save key"}
          </button>
        </div>
      </div>
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
      <Facts id="settings-key-facts" facts={[
        ["Stored", <>In <code>{settings.files.env}</code> on this machine.</>],
        ["Sent", "To that provider, with each model call."],
        ["Never", "Shown again, past its last four characters."],
      ]} />
      <details className="key-more">
        <summary>How your key is handled</summary>
        <p className="settings-help" id="settings-key-help">
          A key starting <code>sk-ant-</code> is Anthropic, <code>sk-or-</code> is OpenRouter; any
          other is OpenAI. Each provider keeps one key, written to{" "}
          <code>{settings.files.env}</code>, and the default moves to the provider of the key you
          save, without a restart. Steps put on another provider stay there. On a later start,{" "}
          <code>--api-key</code> or a key exported in your shell still wins. A key is never shown
          again, only its last four characters.
        </p>
      </details>
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
    <form className="set-card" onSubmit={save} aria-labelledby="settings-models-heading">
      <div className="set-card-head">
        <h2 id="settings-models-heading">Provider and model for each step</h2>
        <span className="set-meta">{settings.nodes.length} steps ask a model</span>
      </div>
      {settings.models_note && <p className="notice">{settings.models_note}</p>}
      <ul className="model-rows">
        {settings.nodes.map((node) => (
          <li key={node.agent}>
            <label htmlFor={`model-${node.agent}`}>
              <span className="model-step">{node.label}</span>
              <span className="model-does">{node.does}</span>
            </label>
            <ModelSelect id={`model-${node.agent}`} value={choices[node.agent] || ""} groups={groups}
              disabled={!hasList} invalid={node.unavailable}
              describedBy={node.unavailable ? `model-${node.agent}-unavailable` : undefined}
              onChange={(v) => setChoices({ ...choices, [node.agent]: v })} />
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
        <div className="set-foot">
          <p className="settings-help">
            Written to <code>{settings.files.llm}</code>, the file the CLI reads. The OpenAI models
            worked with the engine's parameters in a check on 2026-09-20; the Claude ones follow
            Anthropic's documented parameter rules. Whether a model plans well is for the evaluation
            to show.
          </p>
          <button id="settings-save-models" className="settings-save" type="submit"
                  disabled={busy || !Object.keys(changes).length}>
            {busy ? "Saving" : "Save models"}
          </button>
        </div>
      )}
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
    </form>
  );
}

// The hosted demo's key form. Nothing here talks to the server: the key is
// put into this tab's storage and sent as a header with each question. The
// three facts are the promise the code keeps; the disclosure says it in full.
function HostedKeyForm({ apiKeys, onKey, limits, reason }) {
  const cards = providerCards(apiKeys);
  const held = cards.filter((c) => c.held);
  const [chosen, setChosen] = useState(() => (held[0] ? held[0].id : "openai"));
  const [key, setKey] = useState("");
  const [fault, setFault] = useState(null);
  const [status, setStatus] = useState(null);
  const name = (p) => PROVIDER_NAMES[p] || p;
  const chosenHeld = held.some((c) => c.id === chosen);

  const save = (e) => {
    e.preventDefault();
    if (!key.trim()) return;
    if (!looksLikeKey(key)) {
      setFault("That does not look like an API key: expected 20 or more letters, digits, '-' or '_', with no spaces.");
      return;
    }
    setFault(null);
    // A key is kept under the provider its own prefix names; one pasted under
    // another card is filed where it belongs, and the page says so.
    const other = keyMismatch(chosen, key);
    const provider = other || providerForKey(key);
    onKey(provider, key);
    setKey("");
    setChosen(provider);
    setStatus(other
      ? `That key's prefix says ${name(provider)}, so it is kept as the ${name(provider)} key. Ask a question and it goes with it.`
      : `Kept in this browser tab as the ${name(provider)} key. Ask a question and it goes with it.`);
  };

  const forget = (provider) => {
    onKey(provider, "");
    setStatus(`The ${name(provider)} key is cleared from this tab.`);
  };

  return (
    <form className="set-card" onSubmit={save} aria-labelledby="hosted-key-heading">
      <h2 id="hosted-key-heading">API key</h2>
      <fieldset className="provider">
        <legend className="visually-hidden">Provider</legend>
        {cards.map((c) => (
          <label key={c.id} className="prov" data-on={chosen === c.id ? "true" : undefined}>
            <input type="radio" className="visually-hidden" name="hosted-provider" id={`hosted-provider-${c.id}`}
                   value={c.id} checked={chosen === c.id} onChange={() => setChosen(c.id)} />
            {c.label}
            <span>{c.held ? "key set" : "none"}</span>
          </label>
        ))}
      </fieldset>
      {held.length ? (
        <ul className="saved-keys" id="hosted-key-current">
          {held.map((c) => (
            <li className="saved" key={c.id}>
              <span className="pulse" aria-hidden="true" />
              <span>
                {held.length > 1 ? `${c.label} key` : "Key"} ending{" "}
                <span className="mono">{keyTail(apiKeys[c.id]) || "…"}</span> is active in this tab.
              </span>
              <button type="button" className="key-clear" id={`hosted-clear-${c.id}`}
                      onClick={() => forget(c.id)}>
                Clear<span className="visually-hidden"> the {c.label} key</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="settings-current" id="hosted-key-current">
          No key in this tab yet, so questions cannot be answered.
        </p>
      )}
      <div className="key-field">
        <label className="settings-label" htmlFor="hosted-key">
          {chosenHeld ? `Replace the ${name(chosen)} key` : `Paste your ${name(chosen)} key`}
        </label>
        <div className="key-row">
          <input
            id="hosted-key"
            className="key-input"
            type="password"
            value={key}
            placeholder={PLACEHOLDERS[chosen] || "sk-…"}
            autoComplete="off"
            spellCheck={false}
            aria-describedby="hosted-key-facts hosted-key-help"
            onChange={(e) => setKey(e.target.value)}
          />
          <button id="hosted-save-key" className="settings-save" type="submit" disabled={!key.trim()}>
            Use this key
          </button>
        </div>
      </div>
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault">{fault}</p>}
      <Facts id="hosted-key-facts" facts={[
        ["Stored", "This tab's session storage."],
        ["Sent", "In a header with each question."],
        ["Never", "On disk, in logs or in traces."],
      ]} />
      <details className="key-more">
        <summary>How your key is handled</summary>
        <p className="settings-help" id="hosted-key-help">
          {reason} Each key is kept in this tab's <code>sessionStorage</code>, sent in a request
          header of its own with each question, used to call that provider for that one question and
          then dropped: it is never written to a file, an environment variable, a log or a run trace
          on the server, and no other visitor can reach it. Closing the tab clears them. One key is
          all the demo needs; add a second only to put a step on another provider. The demo
          answers only from its own three sample databases
          {limits && limits.questions_per_minute
            ? `, up to ${limits.questions_per_minute} questions a minute and ${limits.questions_per_session} a session`
            : ""}
          . To ask questions of your own data, with no limits and a key that stays on your machine,
          run it locally:{" "}
          <code>pip install "nl2sql-engine[demo]"</code> then <code>nl2sql demo</code>.
        </p>
      </details>
    </form>
  );
}

// The hosted demo's model per step. Like the key, it writes nothing to the
// server: the choice goes into this tab's storage and travels with each
// question. Open from the start on a wide window, where it sits beside the key
// card; shut on a narrow one, where the one key is the whole of the simple path.
function HostedModelsForm({ settings, apiKeys, models, onModel }) {
  const [open] = useState(wide);
  const nodes = settings.nodes || [];
  const providers = settings.providers || [];
  const groups = modelGroups(providers, settings.default_model);
  const missing = providersNeeded(models).filter((p) => !apiKeys[p]);
  const chosen = nodes.filter((n) => models[n.agent]);
  const loose = variableModels(providers, models);
  const summary = chosen.length
    ? `${chosen.length} of ${nodes.length} steps on a model you chose`
    : `all ${nodes.length} steps on ${settings.default_model || "the default model"}`;

  return (
    <details className="set-card step-models" id="hosted-models" open={open}>
      <summary>
        <span className="step-models-title">Model for each step</span>
        <span className="step-models-summary">{summary}</span>
      </summary>
      <p className="settings-help" id="hosted-models-help">
        These {nodes.length} steps put the question to a model; everything else is code. A choice
        is kept in this tab, sent with the question, and needs a key for its provider.
      </p>
      <ul className="model-rows">
        {nodes.map((node) => {
          const value = models[node.agent] || "";
          const provider = value.split(":")[0];
          const needsKey = provider && !apiKeys[provider];
          return (
            <li key={node.agent}>
              <label htmlFor={`hosted-model-${node.agent}`}>
                <span className="model-step">{node.label}</span>
                <span className="model-does">{node.does}</span>
              </label>
              <ModelSelect id={`hosted-model-${node.agent}`} value={value} groups={groups}
                invalid={needsKey} describedBy={needsKey ? `hosted-model-${node.agent}-nokey` : undefined}
                onChange={(v) => onModel(node.agent, v)} />
              {needsKey && (
                <p className="model-unavailable" id={`hosted-model-${node.agent}-nokey`}>
                  {PROVIDER_NAMES[provider] || provider} has no key in this tab. Add one before
                  asking: this step cannot run without it.
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {missing.length > 0 && (
        <p className="settings-warn" id="hosted-models-missing">
          No key in this tab for {missing.map((p) => PROVIDER_NAMES[p] || p).join(" or ")}. A
          question will be refused, naming the step, until one is added.
        </p>
      )}
      {loose.length > 0 && (
        <p className="settings-warn" id="hosted-temperature-note">
          <code>{loose.join(", ")}</code> does not accept temperature 0,
          so a step on it runs at the model's default temperature and varies more from run to run.
        </p>
      )}
    </details>
  );
}

// Two cards in their final shape while GET /api/settings is on its way.
export function SettingsSkeleton() {
  return (
    <div className="settings-grid" aria-busy="true" aria-label="Loading settings">
      {[3, 5].map((rows, i) => (
        <div className="set-card" key={i} aria-hidden="true">
          <span className="sk" style={{ width: 140, height: 18 }} />
          {i === 0 && <span className="sk" style={{ height: 60 }} />}
          {i === 0 && <span className="sk" style={{ height: 44 }} />}
          {Array.from({ length: i === 0 ? 0 : rows }, (_, r) => (
            <div className="sk-mrow" key={r}>
              <span className="sk" style={{ width: `${45 + ((r * 13) % 30)}%` }} />
              <span className="sk" style={{ height: 32 }} />
            </div>
          ))}
          {i === 0 && <span className="sk" style={{ width: "80%" }} />}
        </div>
      ))}
    </div>
  );
}

// The settings page. When the server has settings off it says why instead of
// offering a form that fails.
export default function Settings({ settings, error, onSaved, recorded, apiKeys, onKey, limits,
                                   stepModels, onStepModel }) {
  if (error) {
    return <p className="fault">Settings could not be loaded: {error}</p>;
  }
  if (!settings) return <SettingsSkeleton />;
  if (settings.hosted) {
    // Not "off": there is nothing for the server to save, and the two things
    // the visitor does set -- their keys and the model each step runs on --
    // live in their own browser and travel with each question.
    return (
      <div className="settings-grid">
        <HostedKeyForm apiKeys={apiKeys} onKey={onKey} limits={limits} reason={settings.reason} />
        <HostedModelsForm settings={settings} apiKeys={apiKeys} models={stepModels}
          onModel={onStepModel} />
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
