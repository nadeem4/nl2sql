import React from "react";
import { callTokens, nodeRuns, pretty, readable } from "./run.js";

// One node's internals, read from the run's trace file: every execution of the
// node (attempts, sub-queries), what it read and wrote, its errors, and for an
// LLM node the exact prompt and the raw answer. Long text is folded.

const num = (n) => Number(n || 0).toLocaleString();
const secs = (n) => {
  if (n === undefined || n === null) return "-";
  const s = Number(n);
  if (s >= 1) return `${s.toFixed(2)} s`;
  if (s < 0.001) return "<1 ms";
  return `${Math.round(s * 1000)} ms`;
};

function Fold({ label, value, open, meta }) {
  const text = pretty(value);
  if (!text) return null;
  return (
    <details className="fold" open={open}>
      <summary>
        {label}
        <span className="fold-meta">{meta || `${num(text.length)} characters`}</span>
      </summary>
      <pre className="blob" tabIndex={0}>{text}</pre>
    </details>
  );
}

function prompt(messages) {
  return (messages || []).map((m) => `[${m.role}]\n${pretty(m.content)}`).join("\n\n");
}

function answer(response) {
  if (!response) return "";
  const calls = response.tool_calls || [];
  if (calls.length) return pretty(calls.map((c) => ({ name: c.name, arguments: c.args })));
  return readable(response.content);
}

function Problems({ run }) {
  const items = [
    ...(run.exception ? [{ kind: "error", text: run.exception }] : []),
    ...(run.errors || []).map((e) => ({ kind: "error", text: e.error_code ? `${e.error_code}: ${e.message}` : pretty(e) })),
    ...(run.warnings || []).map((w) => ({ kind: "warning", text: w.error_code ? `${w.error_code}: ${w.message}` : (w.message || pretty(w)) })),
  ];
  if (!items.length) return null;
  return (
    <ul className="problems">
      {items.map((p, i) => (
        <li key={i} data-kind={p.kind}>
          <span className="problem-kind">{p.kind === "error" ? "Error" : "Warning"}</span>
          <span className="problem-text">{p.text}</span>
        </li>
      ))}
    </ul>
  );
}

function LlmCall({ call }) {
  const tokens = callTokens(call);
  return (
    <div className="llm-call">
      <p className="llm-head">
        LLM call {call.key && call.key.call_index}
        <span className="mono">{call.model}</span>
        {tokens !== null && <span>{num(tokens)} tokens</span>}
        <span>{secs(call.duration_s)}</span>
      </p>
      {call.error && <p className="problem-text" data-kind="error">{call.error}</p>}
      <Fold label="Prompt sent" value={prompt(call.messages)} />
      <Fold label="Raw response" value={answer(call.response)} open meta="JSON indented for reading" />
      <Fold label="Parsed result" value={call.parsed} />
    </div>
  );
}

export default function NodeInspector({ name, trace, loading, error, onClose }) {
  const runs = nodeRuns(trace, name);
  return (
    <section className="inspect" id="node-inspector" aria-labelledby="inspect-title" aria-live="polite">
      <div className="inspect-head">
        <h4 id="inspect-title"><span className="mono">{name}</span></h4>
        <p className="inspect-sum">
          {loading ? "Reading the trace" : error ? null : `${runs.length} ${runs.length === 1 ? "run" : "runs"}`}
        </p>
        <button className="inspect-close" onClick={onClose}>Close</button>
      </div>
      {error && <p className="fault">Could not read the trace: {error}</p>}
      {!loading && !error && !runs.length && <p className="station-note">This node has no record in the trace.</p>}
      {runs.map((run) => (
        <article className="inspect-run" key={run.seq} data-status={run.status}>
          <p className="run-head">
            <strong>Attempt {run.attempt}</strong>
            {run.sub_query_id && <span>sub-query <code>{run.sub_query_id}</code></span>}
            <span>{secs(run.duration_s)}</span>
            <span className="run-status">{run.status}</span>
          </p>
          <Problems run={run} />
          {(run.llm_calls || []).map((call, i) => <LlmCall key={i} call={call} />)}
          <Fold label="Inputs" value={run.inputs && Object.keys(run.inputs).length ? run.inputs : null} meta="state fields the node read" />
          <Fold label="Outputs" value={run.outputs} meta="the update the node returned" />
        </article>
      ))}
    </section>
  );
}
