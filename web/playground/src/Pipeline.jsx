import React from "react";
import { countModelSteps, modelUsed, pipelineRows } from "./pipeline.js";
import { pipelinePhases, stepStates, waterfall } from "./pipelinePhases.js";
import { providerName } from "./settings.js";
import { secs } from "./runState.js";

// What runs a question. The page exists because the engine's one claim -- the
// model plans, deterministic code writes and checks the SQL -- is invisible
// while a run is only a list of node names.
//
// The steps fall into three phases: understanding the question, answering each
// sub-query (the SQL agent and its own steps, in a bracket because they repeat),
// and combining and explaining. A step a model decides is a raised row with a
// round mark and its model in a chip; a step code decides is a compact row with
// a square mark. Fill means it ran, a dashed outline that it was passed over.
// After a run each row carries its share of the run's time as a bar, so the
// page doubles as the run's waterfall.
//
// The list itself is the server's (GET /api/pipeline), which reads it from the
// graph's own node names, so this page cannot describe a pipeline that is not
// the one running.

const num = (n) => Number(n || 0).toLocaleString();

const STATE_WORDS = { done: "ran", skipped: "skipped", "not-run": "did not run" };

export function Legend() {
  return (
    <ul className="mark-legend" aria-label="Key">
      <li><span className="pmark" data-kind="model" data-state="done" aria-hidden="true" />Asks a model</li>
      <li><span className="pmark" data-kind="code" data-state="done" aria-hidden="true" />Code</li>
      <li><span className="pmark" data-kind="code" data-state="not-run" aria-hidden="true" />Did not run</li>
      <li><span className="pmark" data-kind="code" data-state="skipped" aria-hidden="true" />Skipped</li>
    </ul>
  );
}

function Step({ row, state, bar, usage, ran }) {
  const model = row.kind === "model";
  const used = row.usage ? modelUsed(usage, row.node) || row.model : row.model;
  const spent = row.usage;
  return (
    <li className="pstep" data-kind={row.kind} data-state={state} data-node={row.node}>
      <span className="pmark" data-kind={row.kind} data-state={state} aria-hidden="true" />
      <div className="pstep-text">
        <h3>
          {row.label}
          {model && (
            <span className="pchip" title={row.provider ? `at ${providerName(row.provider)}` : undefined}>
              {used || "no model set"}
            </span>
          )}
          {row.retried && <span className="pchip is-warn">{spent.calls} calls</span>}
          <span className="visually-hidden">
            {model ? ", asks a model" : ", code"}{ran ? `, ${STATE_WORDS[state]}` : ""}
          </span>
        </h3>
        <p>{row.does}</p>
        <p className="pstep-meta">
          <code>{row.node}</code>
          {spent && (
            <span className="pstep-tokens">
              {num(spent.input_tokens)} in{spent.cached_input_tokens > 0 ? ` (${num(spent.cached_input_tokens)} cached)` : ""}
              {" · "}{num(spent.output_tokens)} out
            </span>
          )}
        </p>
      </div>
      {ran && (
        <div className="wf">
          <div className="track" aria-hidden="true">
            {bar && <i style={{ left: `${bar.start}%`, width: `${bar.width}%` }} />}
          </div>
          <span className="wf-time">{row.ran ? secs(row.seconds) : STATE_WORDS[state]}</span>
        </div>
      )}
    </li>
  );
}

// The page in its final shape while GET /api/pipeline is on its way: the
// legend, three phases and their rows, as bars.
export function PipelineSkeleton() {
  const rows = [3, 4, 2];
  return (
    <div className="pipeline" aria-busy="true" aria-label="Loading the pipeline">
      <div className="sk sk-line" style={{ width: "62%" }} />
      <div className="mark-legend sk-legend" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => <span key={i} className="sk" style={{ width: 96 }} />)}
      </div>
      {rows.map((n, p) => (
        <section className="phase" key={p} aria-hidden="true">
          <div className="phase-head"><span className="sk" style={{ width: 220, height: 16 }} /></div>
          <ol className={p === 1 ? "psteps bracket" : "psteps"}>
            {Array.from({ length: n }, (_, i) => (
              <li key={i} className="pstep" data-kind={i % 2 ? "code" : "model"}>
                <span className="sk sk-mark" />
                <div className="pstep-text">
                  <span className="sk" style={{ width: `${40 + ((i * 17) % 30)}%`, height: 14 }} />
                  <span className="sk" style={{ width: `${60 + ((i * 11) % 25)}%` }} />
                </div>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

export default function Pipeline({ pipeline, error, result, asked }) {
  if (error) {
    return <p className="fault">The pipeline could not be loaded: {error}</p>;
  }
  if (!pipeline) return <PipelineSkeleton />;
  const steps = pipeline.steps || [];
  const ran = Boolean(result && asked);
  const usage = ran ? result.usage : null;
  const rows = pipelineRows(steps, ran ? result : null);
  const phases = pipelinePhases(rows);
  const states = stepStates(rows, ran);
  const bars = ran ? waterfall(rows, result) : {};
  const models = countModelSteps(steps);
  const wall = ran && result.timings ? result.timings.LangGraph : undefined;
  const item = (row) => (
    <Step key={row.node} row={row} state={states[row.node]} bar={bars[row.node]} usage={usage} ran={ran} />
  );

  return (
    <div className="pipeline" data-ran={ran ? "true" : undefined}>
      <p className="pipeline-note">
        {models} of these {rows.length} steps put the question to a model. The rest is ordinary
        code: it finds the schema, writes the SQL from the plan, checks it against the real tables
        and this role's policy, runs it and combines the results.{" "}
        {pipeline.docs && (
          <a href={pipeline.docs} target="_blank" rel="noreferrer">
            The architecture docs have the long version.
          </a>
        )}
      </p>

      <p className="pipeline-state">
        {ran ? (
          <>
            The bars show the last run, <span className="mono">{asked.question}</span>
            {wall !== undefined ? <>, which took {secs(wall)}.</> : "."} Each step is drawn
            for its longest run, end to end in this order.
          </>
        ) : (
          "Nothing has been asked in this tab yet, so each model step shows the model it is set to use. Ask a question and each step's time appears here as a bar."
        )}
      </p>

      <Legend />

      {phases.map((phase, i) => (
        <section className="phase" key={phase.id} aria-labelledby={`phase-${phase.id}`}>
          <div className="phase-head">
            <span className="phase-k" aria-hidden="true">{i + 1}</span>
            <h2 id={`phase-${phase.id}`}>{phase.title}</h2>
            {phase.note && <span className="phase-note">{phase.note}</span>}
          </div>
          <ol className="psteps">
            {phase.items.map((it) => (it.type === "bracket" ? (
              <li key={it.parent.node} className="pbracket">
                <ol className="psteps">{item(it.parent)}</ol>
                <ol className="psteps bracket" aria-label={`Inside ${it.parent.label}`}>
                  {it.rows.map(item)}
                </ol>
              </li>
            ) : item(it.row)))}
          </ol>
        </section>
      ))}
    </div>
  );
}
