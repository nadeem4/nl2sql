import React, { useState } from "react";
import { countRows, joinNames, needsRebuild, relativeTime, shortVersion, sourceNames, statusLine } from "./indexHealth.js";

// Server sentences mark commands with backticks; show them as code.
function withCode(text) {
  return text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part));
}

// The search index behind the resolver: what it holds, whether it matches the
// schema, and a Rebuild that is always on offer. It sits above the Database
// in the rail because the two are easy to confuse: the Database reads the
// schema snapshot, and it can look fine while this index is empty.
export default function IndexPanel({ index, error, onRebuild }) {
  const [enrich, setEnrich] = useState(false);
  const [fault, setFault] = useState(null);
  const [sending, setSending] = useState(false);

  if (error) {
    return (
      <section className="index" id="index-panel" aria-labelledby="index-heading">
        <h2 id="index-heading">Search index</h2>
        <p className="fault">The index status could not be read: {error}</p>
      </section>
    );
  }
  if (!index) {
    return (
      <section className="index" id="index-panel" aria-labelledby="index-heading">
        <h2 id="index-heading">Search index</h2>
        <p className="quiet">Checking the index.</p>
      </section>
    );
  }

  const { health, rebuild, job, folder } = index;
  const running = job.state === "running";
  const broken = needsRebuild(health);
  const ds = (health.datasources || []).find((d) => d.datasource_id === index.datasource_id) || health.datasources?.[0];
  // One index, one heading; with several databases in it, name them instead of
  // the one Rebuild happens to touch.
  const names = sourceNames(health);
  const several = names.length > 1;
  const rows = countRows(health.counts);
  const built = relativeTime(ds?.built_at || health.built_at);

  const start = async () => {
    if (running || sending) return;
    setFault(null);
    setSending(true);
    try {
      await onRebuild(enrich);
    } catch (err) {
      setFault(err.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="index" id="index-panel" aria-labelledby="index-heading" data-status={health.status}>
      <h2 id="index-heading">
        Search index {!several && <code className="ds">{index.datasource_id}</code>}
      </h2>
      {several && (
        <p className="index-sources" id="index-sources">Covers {joinNames(names)}.</p>
      )}
      <p className="index-status" id="index-status" role="status">{statusLine(health)}</p>

      {health.status === "stale" && health.problems.length > 0 && (
        <ul className="index-problems">
          {health.problems.map((p) => <li key={p}>{withCode(p)}</li>)}
        </ul>
      )}

      {rows.length > 0 && (
        <dl className="index-counts" id="index-counts">
          {rows.map((r) => (
            <div key={r.kind}>
              <dt>{r.label}</dt>
              <dd>{r.count.toLocaleString()}</dd>
            </div>
          ))}
        </dl>
      )}

      {health.total > 0 && ds && (ds.index_version || built) && (
        <p className="index-meta">
          {ds.index_version && (
            <span id="index-version" title={ds.index_version}>
              Schema <code>{shortVersion(ds.index_version)}</code>
            </span>
          )}
          {built && (
            <span id="index-built" title={ds.built_at || health.built_at}>Built {built}</span>
          )}
        </p>
      )}

      {folder && folder.warning && (
        <p className="index-folder" id="index-folder-warning">{folder.warning}</p>
      )}

      {rebuild.available ? (
        <div className={`index-action${broken ? " is-urgent" : ""}`}>
          <label className="check index-enrich" htmlFor="index-enrich">
            <input
              id="index-enrich"
              type="checkbox"
              checked={enrich}
              disabled={!rebuild.enrich_available || running}
              aria-describedby="index-enrich-help"
              onChange={(e) => setEnrich(e.target.checked)}
            />
            Write descriptions with the LLM
          </label>
          <p className="index-help" id="index-enrich-help">
            {rebuild.enrich_available
              ? "Spends tokens on your key. Off, the index uses the schema and its statistics only."
              : rebuild.enrich_reason}
          </p>
          <button
            id="index-rebuild"
            className={broken ? "index-rebuild is-primary" : "index-rebuild"}
            onClick={start}
            disabled={running || sending}
          >
            {running ? "Rebuilding" : broken ? "Rebuild the index" : "Rebuild"}
          </button>
          {!running && (
            <p className="index-help">
              {several ? (
                <>
                  Re-reads the schema, then replaces the entries for <code>{index.datasource_id}</code> once the new
                  ones are complete.
                </>
              ) : (
                "Re-reads the schema, then replaces this datasource's entries once the new ones are complete."
              )}{" "}
              Questions keep using the current entries until then.
            </p>
          )}
        </div>
      ) : (
        <p className="index-help" id="index-unavailable">
          Rebuild is off here. {rebuild.reason} From a terminal: <code>nl2sql --env demo index</code>.
        </p>
      )}

      {(running || job.state === "failed") && job.steps.length > 0 && (
        <ol className="index-progress" id="index-progress" aria-live="polite">
          {job.steps.map((step, i) => {
            const current = running && i === job.steps.length - 1;
            return (
              <li key={`${i}-${step}`} data-current={current || undefined}>
                {step}
                {current && <span className="working" aria-hidden="true" />}
              </li>
            );
          })}
        </ol>
      )}
      {job.state === "failed" && job.error && <p className="fault" id="index-error">{job.error}</p>}
      {fault && <p className="fault">{fault}</p>}
    </section>
  );
}
