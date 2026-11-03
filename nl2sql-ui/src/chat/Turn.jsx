import React from "react";
import RowsTable from "../outputs/RowsTable.jsx";
import Output from "../outputs/Output.jsx";
import SqlCard from "../components/SqlCard.jsx";
import { planSections } from "../lib/plan.js";
import Approval from "./Approval.jsx";
import Clarify from "./Clarify.jsx";
import Feedback from "./Feedback.jsx";
import Spine from "./Spine.jsx";
import TracePanel from "./TracePanel.jsx";
import Warnings from "./Warnings.jsx";
import WhyPanel from "./WhyPanel.jsx";
import { stepRef } from "./answers.js";
import { missPrompt, recordedBadge } from "../pages/firstRun.js";
import {
  answerOf, askSteps, errorLine, offersFor, outputsOf, statusLine,
  toolArgs, toolButtons, toolLabel, waitsOf,
} from "./turns.js";

// What the asker said: their words, or the reading they picked, or the action that re-ran or ran something.
function said(turn) {
  if (turn.kind === "choice") return "Picked a meaning";
  if (turn.kind === "retry") return "Ran the question again";
  if (turn.kind === "tool") return (turn.steps[0] && toolLabel(turn.steps[0].call.name)) || "Ran a tool";
  return turn.text;
}

function Fold({ title, children }) {
  return (
    <details className="fold">
      <summary>{title}</summary>
      {children}
    </details>
  );
}

// One answered question: its rows, the warnings, how it was made, and the rating. The folds open on demand, so
// the answer stays first.
function Answer({ step, many, turn, conversationId, meta }) {
  const result = step.result;
  if (!result) {
    if (!step.error) return null;
    const miss = missPrompt(step.error, meta);
    return (
      <p className="fault" role="alert">
        {miss ? miss.text : step.error.message || String(step.error)}
        {miss && miss.action && <> <a href={miss.action.route}>{miss.action.label}</a></>}
      </p>
    );
  }
  // A replayed answer says so: the recording's model on every call and no token count (firstRun.js).
  const badge = recordedBadge(result, meta && meta.recorded_with);
  const sub = (result.sub_queries || [])[0];
  const sections = sub ? planSections(sub.plan) : [];
  return (
    <div className="chat-step">
      {many && step.question && <p className="chat-step-q">{step.question}</p>}
      {badge && <p className="pchip" data-recorded title={badge.title}>{badge.label}</p>}
      {(result.result_manifest || []).length > 0 && <RowsTable result={result} />}
      <Warnings result={result} />
      <Fold title="Why this answer"><WhyPanel result={result} /></Fold>
      {sub && sub.sql && <Fold title="SQL"><SqlCard sql={sub.sql} plan={sub.plan} /></Fold>}
      {sections.length > 0 && (
        <Fold title="Plan">
          <dl className="plan">
            {sections.map((s) => (
              <div key={s.label} className="clause">
                <dt>{s.label}</dt>
                <dd>{s.items.join("; ")}</dd>
              </div>
            ))}
          </dl>
        </Fold>
      )}
      <Fold title="Trace and cost"><TracePanel result={result} /></Fold>
      <Feedback key={result.trace_id} result={result} conversationId={conversationId} stepRef={stepRef(turn, step)} />
    </div>
  );
}

export default function Turn({ turn, conversation, tools, busy, meta = null, onPick, onRetry, onTool }) {
  const { clarification, approvals } = waitsOf(turn, conversation);
  const open = Boolean(clarification) || approvals.length > 0;
  const steps = askSteps(turn);
  const outputs = outputsOf(turn);
  const buttons = toolButtons(turn, conversation, tools);
  const status = statusLine(turn, open);
  const failed = errorLine(turn);
  return (
    <article className="chat-turn" aria-label={`Turn ${turn.index}`}>
      <p className="chat-said">{said(turn)}</p>
      <div className="chat-reply">
        {answerOf(turn) && <p className="answer-text">{answerOf(turn)}</p>}
        {status && !clarification && !approvals.length && <p className="notice">{status}</p>}
        {failed && <p className="fault" role="alert">{failed}</p>}
        {steps.map((step, i) => <Answer key={i} step={step} many={steps.length > 1} turn={turn} conversationId={conversation.id} meta={meta} />)}
        {outputs.map(({ index, output }) => <Output key={index} output={output} />)}
        {clarification && <Clarify clarification={clarification} disabled={busy} onPick={onPick} />}
        {approvals.length > 0 && <Approval needed={approvals} disabled={busy} onRetry={onRetry} />}
        {(buttons.length > 0 || offersFor(turn).length > 0) && (
          <div className="chat-tools" role="group" aria-label="Do more with this answer">
            {buttons.map((b) => (
              <button key={b.name} type="button" className="rate-btn" disabled={busy} onClick={() => onTool(b.name, toolArgs(b.name, turn))}>{b.label}</button>
            ))}
            {offersFor(turn).map((call, i) => (
              <button key={`offer-${i}`} type="button" className="rate-btn" disabled={busy} onClick={() => onTool(call.name, call.args)}>{toolLabel(call.name)}</button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
