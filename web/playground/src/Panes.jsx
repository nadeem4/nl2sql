import React, { useState } from "react";
import NodeInspector from "./NodeInspector.jsx";
import Feedback from "./Feedback.jsx";
import SqlCard from "./SqlCard.jsx";
import RowsTable from "./RowsTable.jsx";
import Ledger, { Totals } from "./Ledger.jsx";
import { planSections } from "./plan.js";
import { deniedTables, humanCheck, nodeLedger, traceFileName, traceUrl } from "./run.js";

// The run reads top to bottom as one sequence: question, plan, checks, SQL,
// rows, cost. Each station is a renderer over the `/api/ask` response. The
// checks sit between the plan and the SQL on purpose: that is where the engine
// stops a plan, before any SQL exists.

function Station({ id, title, state, note, children }) {
  return (
    <section className="station" id={id} data-state={state} aria-labelledby={`${id}-title`}>
      <span className="mark" aria-hidden="true" />
      <div className="station-head">
        <h3 id={`${id}-title`}>{title}</h3>
        {note && <p className="station-note">{note}</p>}
      </div>
      {children && <div className="station-body">{children}</div>}
    </section>
  );
}

// `answered` is the database the resolver picked, and is empty with a single
// database registered: there is then nothing for it to have picked.
function Question({ asked, busy, answered = [] }) {
  return (
    <Station id="pane-question" title="Question" state={asked ? "done" : "idle"}
      note={asked ? null : "Pick a guided question or type your own."}>
      {asked && (
        <>
          <p className="asked">{asked.question}</p>
          <p className="asked-meta">
            Asked as <strong>{asked.role}</strong>
            {asked.planOnly && ", plan only"}
            {busy && <span className="working" role="status">Running the pipeline</span>}
          </p>
          {!busy && answered.length > 0 && (
            <p className="asked-meta" id="answered-from">
              Answered from <strong>{answered.join(" and ")}</strong>, the database the resolver picked.
            </p>
          )}
        </>
      )}
    </Station>
  );
}

export function PlanPane({ sub, state }) {
  const sections = planSections(sub && sub.plan);
  const idle = "The model writes a typed plan here, never SQL.";
  return (
    <Station id="pane-plan" title="Plan" state={state}
      note={state === "idle" ? idle : state === "busy" ? "Waiting for the planner." : sections.length ? null : "No plan came back, so nothing downstream ran."}>
      {sections.length > 0 && (
        <>
          <dl className="plan">
            {sections.map((section) => (
              <div className="clause" key={section.label}>
                <dt>{section.label}</dt>
                <dd>
                  {section.items.map((item, i) => (
                    <code key={i}>{item}</code>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
          {sub.plan.reasoning && <p className="reasoning">{sub.plan.reasoning}</p>}
        </>
      )}
    </Station>
  );
}

function Retry({ sub, result }) {
  if (!sub || !sub.retry_count) return null;
  const rejected = ((result && result.errors) || []).filter((e) => e.error_code !== "SECURITY_VIOLATION");
  const feedback = ((result && result.warnings) || []).filter((w) => w.node === "refiner");
  const attempts = sub.retry_count + 1;
  return (
    <div className="retry">
      <p>
        <strong>{attempts} plans.</strong> The checks rejected {sub.retry_count === 1 ? "the first" : `the first ${sub.retry_count}`}
        {rejected.length > 0 && <>: <span className="quote">{rejected[0].message}</span></>}
      </p>
      {feedback.length > 0 && (
        <p>Refiner feedback to the planner: <span className="quote">{feedback[0].message}</span></p>
      )}
      <p>The checks below are for the plan that ran.</p>
    </div>
  );
}

export function ValidationPane({ sub, result, state, role }) {
  const checks = (sub && sub.validation) || [];
  const errors = (result && result.errors) || [];
  const failed = checks.filter((c) => !c.passed);
  const denied = deniedTables(errors);
  let verdict = null;
  if (failed.length && denied.length) {
    verdict = (
      <div className="verdict">
        <p className="verdict-head">Refused before any SQL was written.</p>
        <p>
          The role <strong>{role}</strong> may not read {list(denied)}. The plan stopped here; the generator never ran.
        </p>
      </div>
    );
  } else if (failed.length) {
    verdict = (
      <div className="verdict">
        <p className="verdict-head">Stopped at the checks. No SQL was written.</p>
        <p>{failed[0].message}</p>
      </div>
    );
  }
  const note =
    state === "idle" ? "Checked against the real schema and your role, before any SQL exists."
      : state === "busy" ? null
        : state === "skipped" ? "Not reached."
          : !checks.length ? "Nothing to check." : null;
  return (
    <Station id="pane-validation" title="Checks" state={state} note={note}>
      {verdict}
      <Retry sub={sub} result={result} />
      {checks.length > 0 && (
        <ul className="checks">
          {checks.map((check, i) => (
            <li key={i} data-passed={check.passed}>
              <span className="check-result">{check.passed ? "Passed" : "Refused"}</span>
              <span className="check-name" title={check.name}>{humanCheck(check.name)}</span>
              <span className="check-msg">{check.message}</span>
            </li>
          ))}
        </ul>
      )}
    </Station>
  );
}

export function SqlPane({ sub, state, refused }) {
  const sql = (sub && sub.sql) || "";
  const note =
    state === "idle" ? "Generated from the approved plan, only after the checks pass."
      : state === "skipped" ? (refused ? "Not written. The plan never passed the checks." : "Not reached.")
        : state === "busy" ? null
          : sql ? null : "No SQL.";
  return (
    <Station id="pane-sql" title="SQL" state={state} note={note}>
      {sql && <SqlCard sql={sql} plan={(sub.retry_count || 0) + 1} />}
    </Station>
  );
}

export function RowsPane({ sub, result, state }) {
  const rows = sub && sub.rows;
  const summary = result && result.final_answer && result.final_answer.summary;
  const planOnly = result && result.status === "plan_only";
  const note =
    state === "idle" ? "The query runs read-only against the database."
      : state === "skipped" ? "Nothing ran."
        : state === "busy" ? null
          : planOnly ? "Plan only: nothing was executed."
            : !rows ? "No rows." : null;
  return (
    <Station id="pane-rows" title="Rows" state={state} note={note}>
      {summary && <p className="answer">{summary}</p>}
      {rows && <RowsTable rows={rows} result={result} />}
    </Station>
  );
}

export const secs = (n) => {
  if (n === undefined || n === null) return "-";
  const s = Number(n);
  if (s >= 1) return `${s.toFixed(2)} s`;
  if (s < 0.001) return "<1 ms";
  return `${Math.round(s * 1000)} ms`;
};

// What the answer cost. The summary is always shown; Debug adds one row per
// node that ran, code nodes included, in execution order. When the run wrote a
// trace, each node name opens that node's internals and the trace downloads.
export function UsagePane({ usage, timings, replay, debug, state, result }) {
  const [picked, setPicked] = useState(null);
  const [trace, setTrace] = useState(null);
  const [traceError, setTraceError] = useState(null);
  const [loading, setLoading] = useState(false);
  const href = traceUrl(result);
  const pick = async (name) => {
    if (picked === name) {
      setPicked(null);
      return;
    }
    setPicked(name);
    if (trace || loading || !href) return;
    setLoading(true);
    setTraceError(null);
    try {
      const response = await fetch(href);
      if (!response.ok) throw new Error(`${href} returned ${response.status}`);
      setTrace(await response.json());
    } catch (e) {
      setTraceError(e.message);
    } finally {
      setLoading(false);
    }
  };
  const total = (usage && usage.total) || null;
  const ledger = nodeLedger(usage, timings);
  const wall = timings && timings.LangGraph;
  if (state !== "done" || (!ledger.length && !total)) {
    return (
      <Station id="pane-usage" title="Cost & time" state={state === "done" ? "done" : state}
        note={state === "busy" ? null : state === "idle" ? "Model calls, tokens and time for this question." : "Nothing was recorded for this run."} />
    );
  }
  const priced = total && total.cost !== null && total.cost !== undefined;
  return (
    <Station id="pane-usage" title="Cost & time" state="done">
      <Totals total={total} wall={wall} />
      {debug && href && (
        <p className="trace-line">
          Select a node to see what it read, what it returned and, for a model call, the exact prompt and answer.
          <a className="download" href={href} download={traceFileName(result)}>Download trace</a>
        </p>
      )}
      {debug && result && (result.sub_queries || []).some((s) => s.plan_source === "cache") && (
        <p className="trace-line" id="plan-cache-hit">
          Plan from the plan cache: no planner call. It was validated again for this role before it ran.
        </p>
      )}
      {debug && !href && result && (
        <p className="trace-line">No trace file was kept for this run. Set <code>TRACE_MODE=always</code> to keep one for every run.</p>
      )}
      {debug && ledger.length > 0 && (
        <Ledger ledger={ledger} total={total} wall={wall} priced={priced} picked={picked} href={href} onPick={pick} />
      )}
      {debug && picked && (
        <NodeInspector name={picked} trace={trace} loading={loading} error={traceError} onClose={() => setPicked(null)} />
      )}
      {replay && (
        <p className="footnote">Replay mode: recorded answers report placeholder token counts, not real usage.</p>
      )}
    </Station>
  );
}

function list(names) {
  const b = names.map((n) => <code key={n}>{n}</code>);
  if (b.length < 2) return b;
  return [...b.slice(0, -1).flatMap((x, i) => (i ? [", ", x] : [x])), " or ", b[b.length - 1]];
}

export default function Run({ asked, result, sub, busy, error, debug, replay, feedback, answered = [] }) {
  const checks = (sub && sub.validation) || [];
  const gateFailed = checks.some((c) => !c.passed);
  const miss = result && result.replay_miss;
  const planOnly = result && result.status === "plan_only";

  // Station states: idle (nothing asked), busy, done, stopped (the gate held
  // the plan), skipped (never reached).
  let s;
  if (!asked) s = { plan: "idle", checks: "idle", sql: "idle", rows: "idle", cost: "idle" };
  else if (busy) s = { plan: "busy", checks: "busy", sql: "busy", rows: "busy", cost: "busy" };
  else if (!result || miss || !sub) {
    const reached = result && !miss ? "done" : "skipped";
    s = { plan: "skipped", checks: "skipped", sql: "skipped", rows: "skipped", cost: reached };
  } else {
    s = {
      plan: "done",
      checks: gateFailed ? "stopped" : "done",
      sql: sub.sql ? "done" : "skipped",
      rows: planOnly ? "held" : sub.rows ? "done" : "skipped",
      cost: "done",
    };
  }
  const fault = error || (result && !miss && !sub && result.errors && result.errors[0] && result.errors[0].message);

  return (
    <div className="spine" data-outcome={!asked ? "idle" : busy ? "busy" : gateFailed ? "refused" : "ran"} aria-busy={busy}>
      <Question asked={asked} busy={busy} answered={answered} />
      {miss && (
        <p className="notice" role="status">
          No recorded answer for this question. Add an API key to ask it live.
        </p>
      )}
      {fault && <p className="fault" role="alert">The run stopped: {fault}</p>}
      <PlanPane sub={sub} state={s.plan} />
      <ValidationPane sub={sub} result={result} state={s.checks} role={asked && asked.role} />
      <SqlPane sub={sub} state={s.sql} refused={gateFailed} />
      <RowsPane sub={sub} result={result} state={s.rows} />
      <UsagePane key={(result && result.trace_id) || "none"} usage={result && result.usage} timings={result && result.timings}
        replay={replay} debug={debug} state={s.cost} result={result} />
      <Feedback key={`rate-${(result && result.trace_id) || "none"}`} result={result} busy={busy} options={feedback} />
    </div>
  );
}
