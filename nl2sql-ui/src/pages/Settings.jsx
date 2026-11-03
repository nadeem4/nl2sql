import React, { useEffect, useState } from "react";
import { createApiKey, getLlm, getProviders, listApiKeys, revokeApiKey, setModels } from "../api/settings.js";
import { PROVIDER_NAMES, modelChanges, modelGroups, parseRoles, serverRows, stepRows } from "../lib/settings.js";
import { keyMismatch, keyTail, looksLikeKey, providerCards, providerForKey } from "../lib/hostedKey.js";
import { providersNeeded } from "../lib/hostedModels.js";
import { isAdmin } from "../lib/caller.js";

// Settings. Two kinds of thing live here. When the server lets a request bring its own model (api.request_llm, D210)
// the page keeps the visitor's keys and the model for each step in this tab and sends them with each question. For an
// administrator it also shows the server's own LLM (changed in memory, not in llm.yaml) and the API keys it issues.
// Admin parts are not offered to anyone else: /meta says who the caller is, and a 403 stays the server's final word.

// Wide enough for the two cards to sit side by side, so the models card can
// start open without pushing the key card off the screen.
const WIDE = "(min-width: 1060px)";
const wide = () => typeof window !== "undefined" && window.matchMedia && window.matchMedia(WIDE).matches;
const message = (err) => (err && err.message) || "Something went wrong.";

const PLACEHOLDERS = { openai: "sk-…", anthropic: "sk-ant-…", openrouter: "sk-or-…" };

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
function HostedModelsForm({ llms, providers, apiKeys, models, onModel }) {
  const [open] = useState(wide);
  const nodes = stepRows(llms);
  const defaultModel = llms.default ? llms.default.model : "";
  const groups = modelGroups(providers, defaultModel);
  const missing = providersNeeded(models).filter((p) => !apiKeys[p]);
  const chosen = nodes.filter((n) => models[n.agent]);
  const summary = chosen.length
    ? `${chosen.length} of ${nodes.length} steps on a model you chose`
    : `all ${nodes.length} steps on ${defaultModel || "the default model"}`;

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
    </details>
  );
}

// The server's own LLM, for an administrator: the default and the agents that do not inherit it. Saving changes only
// the models chosen (every other llm setting stays), in memory and in this process; llm.yaml is read again on a restart.
function ServerLlm({ llms, providers, onSaved }) {
  const rows = serverRows(llms);
  const names = Object.fromEntries(stepRows(llms).map((r) => [r.agent, r]));
  const [edits, setEdits] = useState({});
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [fault, setFault] = useState(null);
  const groups = modelGroups(providers, llms.default.model, { markMissingKeys: true });
  const dirty = Object.keys(edits).length > 0;
  // The default has no "inherit" choice: its selector lists the models, not "Default (...)".
  const defaultGroups = groups.slice(1);

  const save = async (e) => {
    e.preventDefault();
    if (!dirty || busy) return;
    setBusy(true);
    setFault(null);
    setStatus(null);
    try {
      await setModels(modelChanges(llms, edits));
      setEdits({});
      setStatus("Saved in memory, in this server process only. A restart reads llm.yaml again.");
      await onSaved();
    } catch (err) {
      setFault(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="set-card" onSubmit={save} aria-labelledby="server-llm-heading" id="server-llm">
      <div className="set-card-head">
        <h2 id="server-llm-heading">The server's model</h2>
        <span className="set-meta">{rows.length - 1} steps ask a model</span>
      </div>
      <ul className="model-rows">
        {rows.map((row) => {
          const info = names[row.agent];
          const value = row.agent in edits ? edits[row.agent] : (row.agent === "default" || !row.inherited ? row.value : "");
          return (
            <li key={row.agent}>
              <label htmlFor={`server-model-${row.agent}`}>
                <span className="model-step">{row.agent === "default" ? "Default" : info.label}</span>
                <span className="model-does">
                  {row.agent === "default" ? "Every step that has no model of its own." : info.does}
                </span>
              </label>
              <ModelSelect id={`server-model-${row.agent}`} value={value}
                groups={row.agent === "default" ? defaultGroups : groups}
                onChange={(v) => setEdits({ ...edits, [row.agent]: v })} />
            </li>
          );
        })}
      </ul>
      <div className="set-foot">
        <p className="settings-help">
          This replaces the server's whole <code>llm</code> section, in memory only. Each provider's key is read from its
          environment variable, as before. Runs already going finish on the old model.
        </p>
        <button id="server-llm-save" className="settings-save" type="submit" disabled={busy || !dirty}>
          {busy ? "Saving" : "Save models"}
        </button>
      </div>
      <p className="settings-status" role="status">{status}</p>
      {fault && <p className="fault" role="alert">{fault}</p>}
    </form>
  );
}

// The API keys the server issues, for an administrator. A new key's token is shown once, here, and never again.
function ApiKeys() {
  const [keys, setKeys] = useState(null);
  const [fault, setFault] = useState(null);
  const [userId, setUserId] = useState("");
  const [roles, setRoles] = useState("");
  const [tenant, setTenant] = useState("");
  const [label, setLabel] = useState("");
  const [token, setToken] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => listApiKeys().then(setKeys).catch((err) => setFault(message(err)));
  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    if (!userId.trim() || busy) return;
    setBusy(true);
    setFault(null);
    try {
      const made = await createApiKey({
        userId: userId.trim(), roles: parseRoles(roles), tenantId: tenant.trim(), label: label.trim(),
      });
      setToken(made.token);
      setUserId(""); setRoles(""); setTenant(""); setLabel("");
      await load();
    } catch (err) {
      setFault(message(err));
    } finally {
      setBusy(false);
    }
  };
  const revoke = async (id) => {
    setFault(null);
    try {
      await revokeApiKey(id);
      await load();
    } catch (err) {
      setFault(message(err));
    }
  };

  return (
    <section className="set-card" aria-labelledby="api-keys-heading" id="api-keys">
      <h2 id="api-keys-heading">API keys for this server</h2>
      <p className="settings-help">A key lets a program call this server as a user, with the roles you give it.</p>
      {keys && !keys.length && <p className="settings-current">No keys issued yet.</p>}
      {keys && keys.length > 0 && (
        <ul className="saved-keys">
          {keys.map((k) => (
            <li className="saved" key={k.id}>
              <span>
                <span className="mono">{k.user_id}</span>{k.label ? `, ${k.label}` : ""}
                <span className="art-meta">
                  {" "}{k.roles.length ? k.roles.join(", ") : "no roles"}{k.tenant_id ? `, tenant ${k.tenant_id}` : ""}
                  {k.revoked_at ? ", revoked" : ""}
                </span>
              </span>
              {!k.revoked_at && (
                <button type="button" className="key-clear" onClick={() => revoke(k.id)}>
                  Revoke<span className="visually-hidden"> the key for {k.user_id}</span>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {token && (
        <div className="notice" role="status">
          <p>Copy this key now. It is not shown again.</p>
          <p className="mono" id="api-key-token">{token}</p>
          <button type="button" className="key-clear" onClick={() => setToken(null)}>Done</button>
        </div>
      )}
      <form className="conn-fields" onSubmit={create}>
        <div className="conn-field">
          <label htmlFor="api-key-user">User id</label>
          <input id="api-key-user" value={userId} onChange={(e) => setUserId(e.target.value)} autoComplete="off" />
        </div>
        <div className="conn-field">
          <label htmlFor="api-key-roles">Roles</label>
          <input id="api-key-roles" value={roles} onChange={(e) => setRoles(e.target.value)} autoComplete="off" />
          <p className="conn-help">Separate roles with commas or spaces.</p>
        </div>
        <div className="conn-field">
          <label htmlFor="api-key-tenant">Tenant (optional)</label>
          <input id="api-key-tenant" value={tenant} onChange={(e) => setTenant(e.target.value)} autoComplete="off" />
        </div>
        <div className="conn-field">
          <label htmlFor="api-key-label">Label (optional)</label>
          <input id="api-key-label" value={label} onChange={(e) => setLabel(e.target.value)} autoComplete="off" />
        </div>
        <div className="conn-actions">
          <button className="settings-save" id="api-key-create" type="submit" disabled={busy || !userId.trim()}>
            {busy ? "Creating" : "Create key"}
          </button>
        </div>
      </form>
      {fault && <p className="fault" role="alert">{fault}</p>}
    </section>
  );
}

// Two cards in their final shape while GET /llm is on its way.
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
        </div>
      ))}
    </div>
  );
}

const KEY_REASON = "This server lets a request bring its own model key.";

// `caller` and `requestLlm` come from GET /meta; the rest is this tab's keys and step choices.
export default function Settings({ caller = null, requestLlm = false, apiKeys, onKey, limits, stepModels, onStepModel }) {
  const admin = isAdmin(caller);
  const [llms, setLlms] = useState(null);
  const [providers, setProviders] = useState(null);
  const [error, setError] = useState(null);
  const load = () => Promise.all([getLlm(), getProviders()])
    .then(([l, p]) => { setLlms(l); setProviders(p); })
    .catch((err) => setError(message(err)));
  useEffect(() => { load(); }, []);

  if (!requestLlm && !admin) {
    return (
      <div className="settings-off" id="settings-unavailable">
        <h3>Nothing to set here</h3>
        <p>
          The server's model and API keys are changed by an administrator, and this server does not take a model key
          with a question.
        </p>
      </div>
    );
  }
  if (error) return <p className="fault">Settings could not be loaded: {error}</p>;
  if (!llms || !providers) return <SettingsSkeleton />;
  return (
    <div className="settings-grid">
      {requestLlm && <HostedKeyForm apiKeys={apiKeys} onKey={onKey} limits={limits} reason={KEY_REASON} />}
      {requestLlm && (
        <HostedModelsForm llms={llms} providers={providers} apiKeys={apiKeys} models={stepModels} onModel={onStepModel} />
      )}
      {admin && <ServerLlm llms={llms} providers={providers} onSaved={load} />}
      {admin && <ApiKeys />}
    </div>
  );
}
