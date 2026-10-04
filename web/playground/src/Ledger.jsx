import React from "react";
import { barShares } from "./artifacts.js";
import { secs } from "./runState.js";

// What the answer cost, as two artifacts: a five-figure summary, and (Debug)
// one ledger row per node with an inline time bar sized to the slowest node.
// A model node's bar takes the accent; a code node's is dimmed ink.

const num = (n) => Number(n || 0).toLocaleString();
const money = (n) => `$${Number(n).toFixed(4)}`;

export function Totals({ total, wall }) {
  const priced = total && total.cost !== null && total.cost !== undefined;
  const cached = total && total.cached_input_tokens;
  return (
    <dl className="totals">
      <div><dt>Total time</dt><dd>{secs(wall)}</dd></div>
      <div><dt>Waiting on the model</dt><dd>{secs(total && total.latency_s)}</dd></div>
      <div><dt>Model calls</dt><dd>{num(total && total.calls)}</dd></div>
      <div><dt>Input tokens{cached ? `, ${num(cached)} cached` : ""}</dt><dd>{num(total && total.input_tokens)}</dd></div>
      <div><dt>Cost</dt><dd>{priced ? money(total.cost) : "not priced"}</dd></div>
    </dl>
  );
}

function TimeCell({ seconds, bar, model, i = 0 }) {
  return (
    <span className="timecell">
      <span className={model ? "bar" : "bar dim"} aria-hidden="true">
        {bar && <i style={{ "--share": bar.share, "--i": i }} />}
      </span>
      <span>{secs(seconds)}</span>
    </span>
  );
}

export default function Ledger({ ledger, total, wall, priced, picked, href, onPick }) {
  const bars = barShares(ledger.map((r) => (typeof r.seconds === "number" ? r.seconds : null)));
  // Reasoning tokens are a column only when some node spent any.
  const reasoning = ledger.some((r) => r.usage && r.usage.reasoning_tokens);
  return (
    <div className="art art-ledger">
      <div className="art-scroll" tabIndex={0} role="region" aria-label="Per-node tokens and time">
        <table className="ledger-table">
          <caption className="visually-hidden">Per node, in the order the pipeline ran them</caption>
          <thead>
            <tr>
              <th scope="col">Node</th>
              <th scope="col">Calls</th>
              <th scope="col">Input</th>
              <th scope="col">Cached</th>
              <th scope="col">Output</th>
              {reasoning && <th scope="col">Reasoning</th>}
              <th scope="col">LLM time</th>
              <th scope="col" className="time-col">Node time</th>
              {priced && <th scope="col">Cost</th>}
            </tr>
          </thead>
          <tbody>
            {ledger.map((r, i) => {
              const u = r.usage;
              return (
                <tr key={r.name} data-node={r.name} data-depth={r.depth} className={u ? "llm" : "code"}
                  data-picked={picked === r.name ? "true" : undefined}>
                  <th scope="row">
                    {href ? (
                      <button className="node node-link" aria-pressed={picked === r.name} aria-controls="node-inspector"
                        onClick={() => onPick(r.name)}>{r.name}</button>
                    ) : (
                      <span className="node">{r.name}</span>
                    )}
                    {r.retried && <span className="tag" title={`${u.calls} calls`}>retried</span>}
                    {r.name === "sql_agent" && <span className="tag quiet">includes the nodes below</span>}
                  </th>
                  <td>{u ? num(u.calls) : ""}</td>
                  <td>{u ? num(u.input_tokens) : ""}</td>
                  <td>{u ? num(u.cached_input_tokens) : ""}</td>
                  <td>{u ? num(u.output_tokens) : ""}</td>
                  {reasoning && <td>{u ? num(u.reasoning_tokens) : ""}</td>}
                  <td>{u ? secs(u.latency_s) : ""}</td>
                  <td className="time-col"><TimeCell seconds={r.seconds} bar={bars[i]} model={!!u} i={i} /></td>
                  {priced && <td>{u && u.cost !== null && u.cost !== undefined ? Number(u.cost).toFixed(4) : ""}</td>}
                </tr>
              );
            })}
          </tbody>
          {total && (
            <tfoot>
              <tr>
                <th scope="row">Question total</th>
                <td>{num(total.calls)}</td>
                <td>{num(total.input_tokens)}</td>
                <td>{num(total.cached_input_tokens)}</td>
                <td>{num(total.output_tokens)}</td>
                {reasoning && <td>{num(total.reasoning_tokens)}</td>}
                <td>{secs(total.latency_s)}</td>
                <td className="time-col">{secs(wall)}</td>
                {priced && <td>{money(total.cost)}</td>}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
