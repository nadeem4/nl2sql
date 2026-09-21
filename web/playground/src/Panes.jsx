import React from "react";
import { planSections } from "./plan.js";

export function PlanPane({ sub }) {
  const sections = planSections(sub && sub.plan);
  return (
    <section className="panel" id="pane-plan">
      <h2>Plan</h2>
      {!sections.length ? (
        <p className="muted">No plan. The model never writes SQL, so nothing downstream ran.</p>
      ) : (
        sections.map((section) => (
          <div className="plan-section" key={section.label}>
            <h3>{section.label}</h3>
            <ul>
              {section.items.map((item, i) => (
                <li key={i}><code>{item}</code></li>
              ))}
            </ul>
          </div>
        ))
      )}
      {sub && sub.plan && sub.plan.reasoning && <p className="muted reasoning">{sub.plan.reasoning}</p>}
    </section>
  );
}

export function ValidationPane({ sub, result }) {
  const checks = (sub && sub.validation) || [];
  const errors = (result && result.errors) || [];
  return (
    <section className="panel" id="pane-validation">
      <h2>Validation</h2>
      {checks.length ? (
        <ul className="checks">
          {checks.map((check, i) => (
            <li key={i} className={check.passed ? "ok" : "bad"}>
              <span className="mark">{check.passed ? "✓" : "✗"}</span>
              <span className="check-name">{check.name}</span>
              <span className="muted">{check.message}</span>
            </li>
          ))}
        </ul>
      ) : errors.length ? (
        <p className="bad">{errors[0].message}</p>
      ) : (
        <p className="muted">Nothing validated yet.</p>
      )}
    </section>
  );
}

export function SqlPane({ sub }) {
  const sql = (sub && sub.sql) || "";
  return (
    <section className="panel" id="pane-sql">
      <h2>SQL</h2>
      {sql ? <pre>{sql}</pre> : <p className="muted">No SQL. The plan was rejected before generation.</p>}
    </section>
  );
}

export function RowsPane({ sub, result }) {
  const rows = sub && sub.rows;
  const summary = result && result.final_answer && result.final_answer.summary;
  return (
    <section className="panel" id="pane-rows">
      <h2>Rows</h2>
      {summary && <p className="summary">{summary}</p>}
      {result && result.status === "plan_only" && <p className="muted">Plan only: nothing was executed.</p>}
      {!rows ? (
        result && result.status !== "plan_only" && <p className="muted">No rows.</p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="rows">
              <thead>
                <tr>{rows.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {rows.rows.map((row, i) => (
                  <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? "NULL" : String(cell)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            {rows.total_rows} rows
            {rows.rows.length < rows.total_rows && ` (showing the first ${rows.rows.length})`}
          </p>
        </>
      )}
    </section>
  );
}

const num = (n) => Number(n || 0).toLocaleString();
const secs = (n) => (n === undefined ? "-" : `${Number(n).toFixed(2)}s`);

// Per-node LLM calls, tokens and time, plus the question's totals. Node time is
// wall-clock (``timings``); LLM time is the part spent waiting on the model.
export function UsagePane({ usage, timings, replay }) {
  const nodes = (usage && usage.nodes) || {};
  const times = timings || {};
  const names = [...new Set([...Object.keys(times).filter((n) => n !== "LangGraph"), ...Object.keys(nodes)])];
  if (!names.length) return null;
  const total = (usage && usage.total) || {};
  const priced = total.cost !== null && total.cost !== undefined;
  names.sort((a, b) => (times[b] || 0) - (times[a] || 0));
  const row = (label, u, seconds, cls) => (
    <tr key={label} className={cls}>
      <td>{label}</td>
      <td>{u ? num(u.calls) : "-"}</td>
      <td>{u ? num(u.input_tokens) : "-"}</td>
      <td>{u ? num(u.cached_input_tokens) : "-"}</td>
      <td>{u ? num(u.output_tokens) : "-"}</td>
      <td>{u ? num(u.reasoning_tokens) : "-"}</td>
      <td>{u ? secs(u.latency_s) : "-"}</td>
      <td>{secs(seconds)}</td>
      {priced && <td>{u && u.cost !== null ? Number(u.cost).toFixed(4) : "-"}</td>}
    </tr>
  );
  return (
    <section className="panel" id="pane-usage">
      <h2>Cost &amp; time</h2>
      <div className="table-scroll">
        <table className="rows usage">
          <thead>
            <tr>
              <th>Node</th><th>LLM calls</th><th>Input tok</th><th>Cached</th><th>Output tok</th>
              <th>Reasoning</th><th>LLM time</th><th>Node time</th>{priced && <th>Cost</th>}
            </tr>
          </thead>
          <tbody>
            {names.map((n) => row(n, nodes[n], times[n]))}
            {row("Total", total, times.LangGraph, "total")}
          </tbody>
        </table>
      </div>
      {replay && (
        <p className="muted">Replay mode: recorded answers report placeholder token counts, not real usage.</p>
      )}
    </section>
  );
}
