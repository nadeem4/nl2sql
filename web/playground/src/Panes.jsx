import React, { useState } from "react";
import NodeInspector from "./NodeInspector.jsx";
import Feedback from "./Feedback.jsx";
import SqlCard from "./SqlCard.jsx";
import RowsTable from "./RowsTable.jsx";
import Ledger, { Totals } from "./Ledger.jsx";
import { planSections } from "./plan.js";
import { humanCheck, nodeLedger, traceFileName, traceUrl } from "./run.js";
import { describeFault } from "./faults.js";
import { missPrompt, recordedBadge } from "./firstRun.js";
import {
  answerHead, gateReason, gateTimeline, runFault, stationHeads, stationStates, statusStrip, stoppedNote,
} from "./runState.js";

// The run reads answer first, then the sequence that produced it: plan,
// checks, SQL, rows, cost. Each station is a renderer over the `/api/ask`
// response. The checks sit between the plan and the SQL on purpose: that is
// where the engine stops a plan, before any SQL exists.

// A station's head is a small label, its kind (a model decides it, or code
// does) and its time; the mark on the spine is round for a model step and
// square for code. A station that is queued or live says so in its head and
// its mark, so its idle sentence is not shown.
function Station({ id, title, state, note, head = {}, children }) {
  const { kind = "code", tag = null, time = null } = head;
  const quiet = state === "queued" || state === "busy";
  return (
    <section className="station" id={id} data-state={state} data-kind={kind} tabIndex={-1}
      aria-labelledby={`${id}-title`}>
      <span className="mark" aria-hidden="true" />
      <div className="station-head">
        <h3 id={`${id}-title`}>{title}</h3>
        {tag && <span className="kind-tag" data-kind={kind}>{tag}</span>}
        {time && <span className="station-time">{time}</span>}
      </div>
      {note && !quiet && <p className="station-note">{note}</p>}
      {children && <div className="station-body">{children}</div>}
    </section>
  );
}

// Strip items move focus to their station rather than writing a hash: the
// hash belongs to the router.
function jump(id) {
  const target = document.getElementById(id);
  if (!target) return;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  target.focus({ preventScroll: true });
}

// What went wrong, in words a visitor can act on (`faults.js`), with the
// provider's own text folded away underneath.
function Fault({ entry, onAgain }) {
  const said = describeFault(entry);
  const raw = entry.provider_response;
  return (
    <div className="fault-box">
      <div className="fault-head">
        <span className="fault-ico" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round"><circle cx="10" cy="10" r="7.5" /><path d="M10 6v5M10 14h.01" /></svg>
        </span>
        <div className="fault-words">
          <p className="fault-title" role="alert">{said.headline}</p>
          {said.body && <p className="fault-body">{said.body}</p>}
        </div>
      </div>
      <div className="fault-actions">
        {said.action && <a className="fault-action" href={said.action.route}>{said.action.label}</a>}
        {onAgain && <button type="button" className="fault-again" onClick={onAgain}>Ask again</button>}
      </div>
      {raw && (
        <details className="fault-raw">
          <summary>Provider response</summary>
          <pre>{typeof raw === "string" ? raw : JSON.stringify(raw, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

// `#pane-question`: before a run, a line saying what will appear here; once
// asked, the answer header. The answer sentence is the largest text in the
// run, and the strip under it summarises the stations below and jumps to them.
// `answered` is the database the resolver picked, and is empty with a single
// database registered: there is then nothing for it to have picked.
function AnswerHead({ asked, busy, stopped, hosted, meta, result, sub, fault, answered = [], onAgain }) {
  if (!asked) {
    return (
      <section className="answer-head is-idle" id="pane-question" tabIndex={-1} aria-labelledby="pane-question-title">
        <h3 id="pane-question-title" className="answer-label">Answer</h3>
        <p className="answer-idle">
          Pick a suggestion or type your own question. The answer leads here, with every step that produced it below.
        </p>
      </section>
    );
  }
  const head = !busy && !stopped && !fault ? answerHead(result, sub) : null;
  const strip = head ? statusStrip(result, sub) : [];
  const miss = busy ? null : missPrompt(result, meta);
  const badge = busy ? null : recordedBadge(result);
  const state = busy ? "busy" : stopped ? "stopped" : fault ? "fault" : head ? head.kind : "empty";
  return (
    <section className="answer-head" id="pane-question" tabIndex={-1} aria-labelledby="pane-question-title"
      data-state={state}>
      <div className="answer-q">
        <h3 id="pane-question-title" className="answer-label">
          {stopped ? "Stopped" : fault ? "No answer" : "Answer"}
        </h3>
        <span className="asked">{asked.question}</span>
        <span className="role-tag">as {asked.role}{asked.planOnly && ", plan only"}</span>
        {badge && <span className="recorded-badge" id="recorded-badge" title={badge.title}>{badge.label}</span>}
      </div>
      {busy && (
        <div className="answer-wait" aria-hidden="true">
          <span className="sk" style={{ width: "78%" }} />
          <span className="sk" style={{ width: "52%" }} />
        </div>
      )}
      {busy && <p className="answer-note">The answer is written last, after the rows come back.</p>}
      {stopped && (
        <p className="answer-text is-quiet" role="status">{stoppedNote({ hosted })}</p>
      )}
      {fault && <Fault entry={fault} onAgain={onAgain} />}
      {miss && (
        <div className="miss" id="replay-miss" role="status">
          <p className="miss-text">{miss.text}</p>
          {miss.action && <a className="miss-action" id="replay-miss-key" href={miss.action.route}>{miss.action.label}</a>}
        </div>
      )}
      {head && <p className="answer-text" data-kind={head.kind}>{head.text}</p>}
      {strip.length > 0 && (
        <div className="answer-strip" role="group" aria-label="Run summary">
          {strip.map((item, i) => (
            <React.Fragment key={`${item.label}-${i}`}>
              {i > 0 && <span className="sep" aria-hidden="true">/</span>}
              <button type="button" className="strip-item" data-tone={item.tone || undefined}
                onClick={() => jump(item.target)}>{item.label}</button>
            </React.Fragment>
          ))}
        </div>
      )}
      {!busy && answered.length > 0 && (
        <p className="answer-from" id="answered-from">
          Answered from <strong>{answered.join(" and ")}</strong>, the database the resolver picked.
        </p>
      )}
    </section>
  );
}

export function PlanPane({ sub, state, head }) {
  const sections = planSections(sub && sub.plan);
  const idle = "The model writes a typed plan here, never SQL.";
  return (
    <Station id="pane-plan" title="Plan" state={state} head={head}
      note={state === "idle" ? idle : state === "done" && !sections.length ? "No plan came back, so nothing downstream ran." : null}>
      {state === "busy" && (
        <>
          <p className="reasoning is-live">Writing a typed plan from the retrieved tables.</p>
          <div className="plan-skeleton" aria-hidden="true">
            <span>Tables</span><span className="sk" style={{ width: "70%" }} />
            <span>Joins</span><span className="sk" style={{ width: "85%" }} />
            <span>Select</span><span className="sk" style={{ width: "60%" }} />
          </div>
        </>
      )}
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

function Glyph({ ok }) {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ok ? <path d="M2.5 6.5l2.2 2.2 4.8-5" /> : <path d="M3 3l6 6M9 3l-6 6" />}
    </svg>
  );
}

// The gate: the plans the checks saw, why they did what they did, and each
// check as a tile with a glyph, so a pass and a refusal differ in more than
// colour.
export function ValidationPane({ sub, result, state, role, head }) {
  const checks = (sub && sub.validation) || [];
  const timeline = gateTimeline(sub);
  const reason = gateReason(sub, result);
  const note =
    state === "idle" ? "Checked against the real schema and your role, before any SQL exists."
      : state === "done" && !checks.length ? "Nothing to check." : null;
  const body = Boolean(timeline || reason || checks.length);
  return (
    <Station id="pane-validation" title="Checks" state={state} note={note} head={head}>
      {body && (
        <>
          {timeline && (
            <ol className="gate-timeline" aria-label="Plans the checks saw">
              {timeline.map((step) => (
                <li key={step.label} className="gate-pill" data-passed={step.passed}>
                  <Glyph ok={step.passed} />{step.label}
                </li>
              ))}
            </ol>
          )}
          {reason && (
            <p className="gate-why">
              {reason.kind === "denied" ? (
                <>
                  The role <strong>{role}</strong> may not read {list(reason.tables)}. The plan stopped here; the generator never ran.
                </>
              ) : reason.text}
            </p>
          )}
          {checks.length > 0 && (
            <ul className="gate-tiles">
              {checks.map((check, i) => (
                <li key={i} className="gate-tile" data-passed={check.passed}>
                  <span className="gate-glyph"><Glyph ok={check.passed} /></span>
                  <b title={check.name}>
                    {humanCheck(check.name)}
                    <span className="visually-hidden">: {check.passed ? "passed" : "refused"}</span>
                  </b>
                  <span>{check.message}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Station>
  );
}

export function SqlPane({ sub, state, refused, head }) {
  const sql = (sub && sub.sql) || "";
  const note =
    state === "idle" ? "Generated from the approved plan, only after the checks pass."
      : state === "skipped" ? (refused ? "Not written. The plan never passed the checks." : null)
        : state === "busy" ? null
          : sql ? null : "No SQL.";
  return (
    <Station id="pane-sql" title="SQL" state={state} note={note} head={head}>
      {sql && <SqlCard sql={sql} plan={(sub.retry_count || 0) + 1} />}
    </Station>
  );
}

export function RowsPane({ sub, result, state, head }) {
  const rows = sub && sub.rows;
  const planOnly = result && result.status === "plan_only";
  const note =
    state === "idle" ? "The query runs read-only against the database."
      : state === "skipped" ? null
        : state === "busy" ? null
          : planOnly ? "Plan only: nothing was executed."
            : !rows ? "No rows." : null;
  return (
    <Station id="pane-rows" title="Rows" state={state} note={note} head={head}>
      {rows && <RowsTable rows={rows} result={result} />}
    </Station>
  );
}

// What the answer cost. The summary is always shown; Debug adds one row per
// node that ran, code nodes included, in execution order. When the run wrote a
// trace, each node name opens that node's internals and the trace downloads.
export function UsagePane({ usage, timings, replay, debug, state, result, head }) {
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
      <Station id="pane-usage" title="Cost & time" state={state === "done" ? "done" : state} head={head}
        note={state === "busy" ? null : state === "idle" ? "Model calls, tokens and time for this question." : "Nothing was recorded for this run."} />
    );
  }
  const priced = total && total.cost !== null && total.cost !== undefined;
  return (
    <Station id="pane-usage" title="Cost & time" state="done" head={head}>
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
      {(replay || (result && result.recorded)) && (
        <p className="footnote">Recorded run: replayed answers report placeholder token counts, not real usage.</p>
      )}
    </Station>
  );
}

function list(names) {
  const b = names.map((n) => <code key={n}>{n}</code>);
  if (b.length < 2) return b;
  return [...b.slice(0, -1).flatMap((x, i) => (i ? [", ", x] : [x])), " or ", b[b.length - 1]];
}

// `stopped` is a run the visitor cancelled; `onAgain` asks the same question
// again, offered beside a fault; `hosted` adds that a stopped question still
// counts toward the session's limit.
export default function Run({ asked, result, sub, busy, stopped, hosted = false, meta = null, error, debug, replay, feedback, answered = [], onAgain }) {
  const s = stationStates({ asked, busy, stopped, result, sub });
  const heads = stationHeads(result, s);
  const gateFailed = s.checks === "stopped";
  // A failed request carries only a sentence; an engine error carries a code.
  const fault = stopped ? null : error ? { message: error } : runFault(result, sub);
  const outcome = !asked ? "idle" : busy ? "busy" : stopped ? "stopped" : gateFailed ? "refused" : "ran";

  return (
    <>
      <AnswerHead asked={asked} busy={busy} stopped={stopped} hosted={hosted} meta={meta} result={result} sub={sub} fault={fault}
        answered={answered} onAgain={onAgain} />
      <div className="spine" data-outcome={outcome} aria-busy={busy}>
        {busy && <span className="spine-run" aria-hidden="true" />}
        <PlanPane sub={sub} state={s.plan} head={heads.plan} />
        <ValidationPane sub={sub} result={result} state={s.checks} role={asked && asked.role} head={heads.checks} />
        <SqlPane sub={sub} state={s.sql} refused={gateFailed} head={heads.sql} />
        <RowsPane sub={sub} result={result} state={s.rows} head={heads.rows} />
        <UsagePane key={(result && result.trace_id) || "none"} usage={result && result.usage} timings={result && result.timings}
          replay={replay} debug={debug} state={s.cost} result={result} head={heads.cost} />
        <Feedback key={`rate-${(result && result.trace_id) || "none"}`} result={result} busy={busy} options={feedback} />
      </div>
    </>
  );
}
