import React, { useCallback, useEffect, useState } from "react";
import { getSchema, listDatasources } from "../api/connect.js";
import { listProposals, showKnowledge } from "../api/knowledge.js";
import { mayAdminister } from "../lib/caller.js";
import Drift from "../knowledge/Drift.jsx";
import Glossary from "../knowledge/Glossary.jsx";
import Proposals from "../knowledge/Proposals.jsx";

// Knowledge: what the engine knows about a datasource (anyone), the proposals waiting for review and the drift check
// (admins: /meta says whether the caller is one, so the sections are not asked for otherwise, and a 403 from
// GET /proposals still hides them). A response for a
// datasource that is no longer selected is dropped.
const message = (err) => (err && err.message) || "Something went wrong.";

function Schema({ id }) {
  const [schema, setSchema] = useState(null);
  useEffect(() => {
    let current = true;
    setSchema(null);
    getSchema(id).then((s) => current && setSchema(s)).catch(() => current && setSchema(false));
    return () => { current = false; };
  }, [id]);
  if (schema === false) return <p className="quiet">The schema could not be read.</p>;
  if (!schema) return null;
  return (
    <details className="kn-section">
      <summary>Schema <span className="art-meta">{schema.tables.length} tables</span></summary>
      <ul className="kn-list">
        {schema.tables.map((t) => (
          <li key={`${t.schema}.${t.name}`}>
            <span className="mono">{t.schema ? `${t.schema}.${t.name}` : t.name}</span>
            <span className="art-meta"> {t.columns.map((c) => c.name).join(", ")}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

export default function Knowledge({ caller = null }) {
  const [sources, setSources] = useState(null);
  const [current, setCurrent] = useState("");
  const [view, setView] = useState(null);
  const [viewFault, setViewFault] = useState(null);
  const [proposals, setProposals] = useState(null);
  const [proposalFault, setProposalFault] = useState(null);
  const [fault, setFault] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    listDatasources()
      .then((list) => { setSources(list); if (list.length) setCurrent(list[0].id); })
      .catch((err) => setFault(message(err)));
  }, []);

  useEffect(() => {
    if (!current) return undefined;
    let live = true;
    setView(null); setViewFault(null);
    showKnowledge(current)
      .then((views) => live && setView(views[0] || null))
      .catch((err) => live && setViewFault(message(err)));
    return () => { live = false; };
  }, [current]);

  useEffect(() => {
    if (!current || !mayAdminister(caller)) return undefined;
    let live = true;
    setProposalFault(null);
    listProposals(current)
      .then((list) => live && setProposals(list))
      .catch((err) => { if (live && err.status !== 403) setProposalFault(message(err)); });
    return () => { live = false; };
  }, [current, reload, caller]);

  useEffect(() => { setProposals(null); }, [current]);

  const changed = useCallback(() => setReload((n) => n + 1), []);

  if (fault) return <p className="fault" role="alert">{fault}</p>;
  if (!sources) return <p className="quiet" role="status">Loading.</p>;
  if (!sources.length) return <p className="quiet">No datasources yet. <a href="#/connect">Connect one</a>.</p>;

  return (
    <div className="kn">
      <div className="conn-field">
        <label className="settings-label" htmlFor="kn-source">Datasource</label>
        <select id="kn-source" value={current} onChange={(e) => setCurrent(e.target.value)}>
          {sources.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
        </select>
      </div>
      <section className="conn-card" aria-labelledby="kn-known-h">
        <h3 id="kn-known-h">What the engine knows</h3>
        {viewFault && <p className="fault" role="alert">{viewFault}</p>}
        {!viewFault && (view ? <Glossary view={view} /> : <p className="quiet" role="status">Loading.</p>)}
        <Schema id={current} />
      </section>
      {proposalFault && <p className="fault" role="alert">{proposalFault}</p>}
      {proposals && (
        <section className="conn-card" aria-labelledby="kn-prop-h">
          <h3 id="kn-prop-h">Waiting for review</h3>
          <Proposals key={current} proposals={proposals} onChanged={changed} />
        </section>
      )}
      {proposals && (
        <section className="conn-card" aria-labelledby="kn-drift-h">
          <h3 id="kn-drift-h">Drift</h3>
          <Drift datasourceIds={sources.map((d) => d.id)} />
        </section>
      )}
    </div>
  );
}
