import React, { useEffect, useRef, useState } from "react";
import { asText, mmrLine, passedOver, picksInOrder, score, searchTitle, shortType } from "./retrieval.js";

// What a vector search saw and how MMR chose: the pool nearest first, the
// picks marked in the accent (as the Database marks the tables a plan reads),
// the dropped entries quiet. The same table serves a run's trace (Debug) and
// the Retrieval inspector.

function Similarity({ value, withheld }) {
  if (withheld) return <span className="quiet" title={withheld}>withheld</span>;
  const share = typeof value === "number" ? Math.max(0, Math.min(1, value)) : 0;
  return (
    <>
      {score(value)}
      <span className="share" style={{ "--share": share }} aria-hidden="true" />
    </>
  );
}

export function SearchTable({ search, withText }) {
  const pool = search.pool || [];
  const skipped = passedOver(search);
  const picks = picksInOrder(search);
  return (
    <div className="search">
      <p className="search-line">{mmrLine(search)}</p>
      {picks.length > 0 && (
        <p className="search-line">
          Picks in order:{" "}
          {picks.map((e, i) => (
            <React.Fragment key={e.id}>
              {i > 0 && ", "}
              <code>{e.label || e.id}</code>
            </React.Fragment>
          ))}
          .
          {skipped.length > 0 && (
            <>
              {" "}Passed over, although closer to the query, because {skipped.length === 1 ? "it repeats" : "each repeats"} an earlier pick:{" "}
              {skipped.map((e, i) => (
                <React.Fragment key={e.id}>
                  {i > 0 && ", "}
                  <code>{e.label || e.id}</code>
                </React.Fragment>
              ))}
              .
            </>
          )}
        </p>
      )}
      {pool.length > 0 && (
        <div className="table-scroll pool-wrap" tabIndex={0} role="region" aria-label={`${searchTitle(search)} pool`}>
          <table className="grid pool">
            <thead>
              <tr>
                <th scope="col" className="num">#</th>
                <th scope="col">Entry</th>
                <th scope="col">Type</th>
                <th scope="col" className="num" title="Cosine similarity to the query">Similarity</th>
                <th scope="col">MMR</th>
                <th scope="col" className="num" title="The MMR score each pick won with">Score</th>
                <th scope="col" className="num" title="Similarity to the closest earlier pick">Overlap</th>
              </tr>
            </thead>
            <tbody>
              {pool.map((e) => (
                <tr key={e.id} data-picked={e.picked || undefined}>
                  <td className="num">{e.rank}</td>
                  <th scope="row" className="entry">
                    <span className="entry-name" title={e.id}>{e.label || e.id}</span>
                    {withText && e.text && <span className="entry-text" title={e.text}>{e.text}</span>}
                  </th>
                  <td className="entry-type">{shortType(e.type)}</td>
                  <td className="num sim-col"><Similarity value={e.similarity} withheld={e.withheld} /></td>
                  <td className="pick">{e.picked ? `pick ${e.pick_order}` : "dropped"}</td>
                  <td className="num">{e.picked ? score(e.mmr_score) : ""}</td>
                  <td className="num">{e.picked && e.pick_order > 1 ? score(e.redundancy) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FinalTables({ tables }) {
  if (!tables || !tables.length) return null;
  return (
    <p className="search-line">
      Sent to the planner: {tables.map((t, i) => (
        <React.Fragment key={t.table}>
          {i > 0 && ", "}
          <code>{t.table}</code>
          <span className="quiet"> ({t.columns.length} {t.columns.length === 1 ? "column" : "columns"})</span>
        </React.Fragment>
      ))}.
    </p>
  );
}

// A node's retrieval record from the run trace (datasource resolver, schema retriever).
export function RetrievalRecord({ retrieval }) {
  if (!retrieval) return null;
  const searches = retrieval.searches || [];
  return (
    <section className="retrieval-record" aria-label="Retrieval">
      <h5>Retrieval</h5>
      {retrieval.skipped ? (
        <p className="search-line">No vector search ran. {retrieval.reason}</p>
      ) : (
        <p className="search-line">
          Query embedded: <code className="query">{retrieval.query}</code>
        </p>
      )}
      {!retrieval.skipped && !searches.length && <p className="search-line">The search did not run to completion; see the errors above.</p>}
      {searches.map((s, i) => (
        <div className="search-block" key={i}>
          <h6>{searchTitle(s)}</h6>
          <SearchTable search={s} />
        </div>
      ))}
      <FinalTables tables={retrieval.tables} />
    </section>
  );
}

const TYPE_LABELS = { "schema.datasource": "Datasources", "schema.table": "Tables", "schema.column": "Columns",
  "schema.relationship": "Joins", "schema.metric": "Metrics" };

// The Retrieval inspector: any text against the live index, with the knobs the
// engine fixes (k, lambda, which entry types, which datasource) exposed. The
// embedder is local, so every search is free; after the first one, changing a
// knob searches again.
export default function RetrievalInspector({ options, error, question }) {
  const defaults = (options && options.defaults) || { k: 8, lambda_mult: 0.7, fetch_multiplier: 4 };
  const [query, setQuery] = useState(question || "");
  const [k, setK] = useState(defaults.k);
  const [lambda, setLambda] = useState(defaults.lambda_mult);
  const [types, setTypes] = useState(["schema.table", "schema.column"]);
  const [datasource, setDatasource] = useState(options ? options.datasource_id || "" : "");
  const [result, setResult] = useState(null);
  const [fault, setFault] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const searched = useRef(false);

  const search = async () => {
    const text = query.trim();
    if (!text) return;
    setBusy(true);
    setFault(null);
    try {
      const response = await fetch("/api/retrieval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: text, k: Number(k), lambda_mult: Number(lambda), types, datasource_id: datasource || null }),
      });
      const reply = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof reply.detail === "string" ? reply.detail : `The server answered ${response.status}.`);
      }
      setResult(reply);
      setCopied(false);
      searched.current = true;
    } catch (err) {
      setFault(err.message);
    } finally {
      setBusy(false);
    }
  };

  // Once there is a result, each knob re-runs the search, shortly after the
  // last change so dragging lambda does not send a request per step.
  useEffect(() => {
    if (!searched.current) return undefined;
    const timer = setTimeout(search, 250);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [k, lambda, types, datasource]);

  if (error) return <p className="fault">The inspector could not be read: {error}</p>;
  if (!options) return <p className="quiet">Checking the index.</p>;
  if (!options.available) {
    return (
      <div className="settings-off" id="retrieval-unavailable">
        <h3>The Retrieval inspector is off here</h3>
        <p>{options.reason}</p>
      </div>
    );
  }

  const toggleType = (t) => setTypes((now) => options.types.filter((x) => (x === t ? !now.includes(t) : now.includes(x))));
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(asText(result));
      setCopied(true);
    } catch {
      setFault("The browser did not allow copying. Select the table and copy it instead.");
    }
  };

  return (
    <div className="inspector">
      <form className="inspector-form" onSubmit={(e) => { e.preventDefault(); search(); }}>
        <label className="settings-label" htmlFor="retrieval-query">Text to embed</label>
        <div className="settings-row">
          <input id="retrieval-query" value={query} autoComplete="off" spellCheck={false}
            placeholder="Which genre sells the most tracks?" onChange={(e) => setQuery(e.target.value)} />
          <button id="retrieval-search" className="settings-save" type="submit" disabled={busy || !query.trim()}>
            {busy ? "Searching" : "Search"}
          </button>
        </div>
        <div className="knobs">
          <div className="knob">
            <label htmlFor="retrieval-k">Picks (k)</label>
            <input id="retrieval-k" type="number" min={1} max={50} value={k}
              onChange={(e) => setK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} />
            <span className="knob-help">pool {k * defaults.fetch_multiplier}</span>
          </div>
          <div className="knob knob-lambda">
            <label htmlFor="retrieval-lambda">Lambda <output htmlFor="retrieval-lambda">{Number(lambda).toFixed(2)}</output></label>
            <input id="retrieval-lambda" type="range" min={0} max={1} step={0.05} value={lambda}
              aria-describedby="retrieval-lambda-help" onChange={(e) => setLambda(Number(e.target.value))} />
            <span className="knob-help" id="retrieval-lambda-help">0 favours difference, 1 favours similarity. The engine uses {defaults.lambda_mult}.</span>
          </div>
          <div className="knob">
            <label htmlFor="retrieval-datasource">Datasource</label>
            <select id="retrieval-datasource" value={datasource} onChange={(e) => setDatasource(e.target.value)}>
              <option value="">All datasources</option>
              {(options.datasources || []).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <fieldset className="knob types">
            <legend>Entry types</legend>
            {options.types.map((t) => (
              <label className="check" key={t} htmlFor={`retrieval-type-${shortType(t)}`}>
                <input id={`retrieval-type-${shortType(t)}`} type="checkbox" checked={types.includes(t)} onChange={() => toggleType(t)} />
                {TYPE_LABELS[t] || shortType(t)}
              </label>
            ))}
            {types.length === 0 && <span className="knob-help">none ticked searches every type</span>}
          </fieldset>
        </div>
      </form>
      {fault && <p className="fault" id="retrieval-error">{fault}</p>}
      {result && (
        <div className="inspector-result" id="retrieval-result" aria-live="polite" aria-busy={busy}>
          <div className="inspector-result-head">
            <p className="search-line">Query embedded: <code className="query">{result.query}</code></p>
            <button id="retrieval-copy" type="button" className="inspect-close" onClick={copy} disabled={!result.pool.length}>
              {copied ? "Copied" : "Copy as text"}
            </button>
          </div>
          <SearchTable search={result} withText />
        </div>
      )}
    </div>
  );
}
