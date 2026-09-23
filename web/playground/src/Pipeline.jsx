import React from "react";
import { countModelSteps, modelUsed, pipelineRows } from "./pipeline.js";
import { providerName } from "./settings.js";
import { secs } from "./Panes.jsx";

// What runs a question. The page exists because the engine's one claim -- the
// model plans, deterministic code writes and checks the SQL -- is invisible
// while a run is only a list of node names.
//
// It borrows the run's own vocabulary: a spine, a mark per step, the node name
// in mono so a row here and a row in the Debug ledger are plainly the same
// thing. The accent fills the marks of the steps a model decides, exactly as
// it marks the path a question travels elsewhere. Before a run each model step
// shows the model it is set to use; after one, what that step actually spent.
//
// The list itself is the server's (GET /api/pipeline), which reads it from the
// graph's own node names, so this page cannot describe a pipeline that is not
// the one running.

const num = (n) => Number(n || 0).toLocaleString();

function Tokens({ usage }) {
  return (
    <dl className="step-tokens">
      <div><dt>In</dt><dd>{num(usage.input_tokens)}</dd></div>
      {usage.cached_input_tokens > 0 && (
        <div><dt>Cached</dt><dd>{num(usage.cached_input_tokens)}</dd></div>
      )}
      <div><dt>Out</dt><dd>{num(usage.output_tokens)}</dd></div>
    </dl>
  );
}

function Step({ row, usage, showRun }) {
  const spent = row.usage;
  const used = spent ? modelUsed(usage, row.node) || row.model : null;
  return (
    <li className="step" data-kind={row.kind} data-depth={row.depth} data-ran={row.ran ? "true" : undefined}>
      <span className="mark" aria-hidden="true" />
      <div className="step-head">
        <h3>{row.label}</h3>
        <code className="step-node">{row.node}</code>
        <span className="step-kind">{row.kind === "model" ? "a model decides this" : "code"}</span>
      </div>
      <p className="step-does">{row.does}</p>
      {row.kind === "model" && (
        <p className="step-model">
          {used ? <>Ran on <b className="mono">{used}</b></>
                : row.model ? <>Set to <b className="mono">{row.model}</b></>
                : "No model is configured for this step."}
          {row.provider && (used || row.model) ? <> at {providerName(row.provider)}.</> : null}
        </p>
      )}
      {showRun && (
        <div className="step-run">
          {spent && <Tokens usage={spent} />}
          <p className="step-time">
            {row.ran ? secs(row.seconds) : "did not run"}
            {row.retried && <span className="tag">{spent.calls} calls, retried</span>}
          </p>
        </div>
      )}
    </li>
  );
}

export default function Pipeline({ pipeline, error, result, asked }) {
  if (error) {
    return <p className="fault">The pipeline could not be loaded: {error}</p>;
  }
  if (!pipeline) {
    return <p className="settings-help">Loading the pipeline</p>;
  }
  const steps = pipeline.steps || [];
  const ran = Boolean(result && asked);
  const usage = ran ? result.usage : null;
  const rows = pipelineRows(steps, ran ? result : null);
  const models = countModelSteps(steps);
  const wall = ran && result.timings ? result.timings.LangGraph : undefined;

  return (
    <div className="pipeline">
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
            Showing the last run, <span className="mono">{asked.question}</span>
            {wall !== undefined ? <>, which took {secs(wall)}.</> : "."}
          </>
        ) : (
          "Nothing has been asked in this tab yet, so each model step shows the model it is set to use. Ask a question and its tokens and time appear here."
        )}
      </p>

      <ol className="steps spine">
        {rows.map((row) => <Step key={row.node} row={row} usage={usage} showRun={ran} />)}
      </ol>
    </div>
  );
}
