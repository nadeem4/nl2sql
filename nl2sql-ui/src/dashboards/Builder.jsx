import React, { useEffect, useId, useRef, useState } from "react";
import { listDatasources } from "../api/chat.js";
import { builderOptions, proposeTiles, startBuildJob } from "../api/dashboards.js";
import { cancelJob, followJob } from "../api/jobs.js";
import { getMeta } from "../api/meta.js";
import { answerDate } from "../chat/answers.js";
import JobProgress from "../components/JobProgress.jsx";
import { readKeys } from "../lib/hostedKey.js";
import {
  askedBlock, buildBody, buildFlow, canAdvance, describeBuilderError, effectiveState, emptyState, canReword, setChart, CHARTS, proposalsBody, reword, skippedLines,
  sourceChoices, tick, toggle, withTiles,
} from "./builder.js";

const api = { startJob: startBuildJob, follow: followJob };
const running = (job) => job && ["queued", "running"].includes(job.status);
const CHART_NAMES = { auto: "Auto", line: "Line", bar: "Bar", big_number: "Big number", table: "Table" };
const NAMES = { sources: "Sources", inputs: "What to ask", review: "Review", build: "Build" };

function Chips({ label, items, chosen, onToggle, disabled }) {
  if (!items.length) return null;
  const id = useId();
  return (
    <div className="build-group" role="group" aria-labelledby={id}>
      <p className="build-label" id={id}>{label}</p>
      <ul className="chip-list">
        {items.map((i) => (
          <li key={i.id}>
            <button type="button" className="chip" aria-pressed={chosen.includes(i.id)} disabled={disabled} title={i.title} onClick={() => onToggle(i.id)}>
              {i.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// A four-step panel that makes a dashboard from the questions a person picks or describes: sources, chips, a list of
// tiles to tick and reword, then a build job that answers each tile as the viewer. Generate (no model) stays beside it.
export default function Builder({ workspaces, selected, onOpen }) {
  const [step, setStep] = useState("sources");
  const [state, setState] = useState(emptyState(""));
  const [datasources, setDatasources] = useState(null);
  const [options, setOptions] = useState(null);
  const [saved, setSaved] = useState([]);
  const [block, setBlock] = useState(null);
  const [job, setJob] = useState(null);
  const [report, setReport] = useState(null);
  const [fault, setFault] = useState(null);
  const [busy, setBusy] = useState(false);
  const abort = useRef(new AbortController());
  const heading = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    abort.current = controller;
    listDatasources()
      .then((all) => {
        const ready = all.filter((d) => !String(d.status || "").startsWith("failed"));
        setDatasources(ready);
        if (ready[0]) setState((s) => (s.datasourceId ? s : emptyState(ready[0].id)));
      })
      .catch((e) => { setDatasources([]); setFault(describeBuilderError(e)); });
    getMeta().then((meta) => setBlock(askedBlock(meta, readKeys(window.sessionStorage)))).catch(() => {});
    return () => controller.abort();
  }, []);

  // Next, Back and Build unmount the button that was pressed; move focus to the new step's heading.
  useEffect(() => { if (heading.current) heading.current.focus(); }, [step]);

  const datasourceId = state.datasourceId;
  useEffect(() => {
    if (!datasourceId) return undefined;
    let current = true;
    setOptions(null);
    builderOptions(datasourceId)
      .then((o) => { if (current) { setOptions(o); setSaved(o.saved || []); } })
      .catch((e) => { if (current) { setOptions({ curated: [], saved: [], metrics: [], dimensions: [], time_columns: [], ranges: [] }); setFault(describeBuilderError(e)); } });
    return () => { current = false; };
  }, [datasourceId]);

  const set = (patch) => setState((s) => ({ ...s, ...patch }));
  const sources = sourceChoices(block);
  const chosen = effectiveState(state, block);
  const into = (workspaces || []).some((w) => w.id === state.workspaceId) ? state.workspaceId : selected || "";
  const index = ["sources", "inputs", "review", "build"].indexOf(step);

  const go = async (next) => {
    setFault(null);
    if (step === "inputs" && next === "review") {
      setBusy(true);
      try {
        const tiles = await proposeTiles(proposalsBody(chosen));
        setState((s) => withTiles(s, tiles));
        setStep("review");
      } catch (e) {
        setFault(describeBuilderError(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    setStep(next);
  };

  const build = async () => {
    setFault(null);
    setReport(null);
    setBusy(true);
    setStep("build");
    try {
      const out = await buildFlow(api, { ...chosen, workspaceId: into }, {
        onJob: setJob, onStarted: () => {}, signal: abort.current.signal,
      });
      if (out.report) {
        if (out.report.skipped && out.report.skipped.length) setReport(out);
        else onOpen(out.hash);
      }
    } catch (e) {
      if (e.name !== "AbortError") setFault(describeBuilderError(e, "build"));
    } finally {
      setBusy(false);
    }
  };

  const back = () => { setStep("review"); setJob(null); setReport(null); setFault(null); };
  const restart = () => { setStep("sources"); setJob(null); setReport(null); setFault(null); set({ tiles: [] }); };
  const over = Boolean(job) && !running(job);
  const advance = step !== "build" && step !== "review";

  return (
    <details className="dash-gen dash-build">
      <summary>Build a dashboard</summary>
      <div className="dash-gen-form">
        <ol className="build-steps" aria-label="Steps">
          {["sources", "inputs", "review", "build"].map((s, i) => (
            <li key={s} aria-current={s === step ? "step" : undefined} data-done={i < index}>{NAMES[s]}</li>
          ))}
        </ol>

        <h3 className="build-step-title" tabIndex={-1} ref={heading}>{NAMES[step]}</h3>

        {step === "sources" && (
          <>
            <div className="field">
              <label htmlFor="build-source">Datasource</label>
              <select id="build-source" value={state.datasourceId} disabled={!datasources || datasources.length === 0}
                onChange={(e) => setState(emptyState(e.target.value))}>
                {(datasources || []).map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
              </select>
            </div>
            {datasources && datasources.length === 0 && !fault && <p className="quiet">No datasource is ready. Connect one first.</p>}
            <p className="page-note">Pick where the charts come from. You can mix them.</p>
            <ul className="build-sources">
              {sources.map((c) => (
                <li key={c.id}>
                  <label className="check">
                    <input type="checkbox" checked={chosen.sources.includes(c.id)} disabled={c.disabled}
                      onChange={() => set({ sources: toggle(state.sources, c.id) })} />
                    {" "}{c.label}
                  </label>
                  <span className="dash-meta">{c.reason || c.hint}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        {step === "inputs" && !options && <p className="page-note">Reading what this datasource offers</p>}
        {step === "inputs" && options && (
          <>
            {chosen.sources.includes("curated") && (
              <Chips label="Curated questions" chosen={state.curatedIds} onToggle={(id) => set({ curatedIds: toggle(state.curatedIds, id) })}
                items={options.curated.map((q) => ({ id: q.id, label: q.question }))} />
            )}
            {chosen.sources.includes("saved") && (
              <Chips label="My saved answers" chosen={state.savedIds} onToggle={(id) => set({ savedIds: toggle(state.savedIds, id) })}
                items={saved.map((a) => ({ id: a.id, label: a.question, title: answerDate(a) }))} />
            )}
            {chosen.sources.includes("curated") && options.curated.length === 0 && <p className="quiet">No curated questions yet.</p>}
            {chosen.sources.includes("saved") && saved.length === 0 && <p className="quiet">Nothing kept yet. Choose Yes under an answer in chat.</p>}
            {chosen.sources.includes("asked") && (
              <>
                <p className="page-note">Up to 3 new questions are written for you.</p>
                <Chips label="What matters" chosen={state.metrics} onToggle={(id) => set({ metrics: toggle(state.metrics, id) })}
                  items={options.metrics.map((m) => ({ id: m.term, label: m.term, title: m.definition || undefined }))} />
                <Chips label="Break down by" chosen={state.dimensions} onToggle={(id) => set({ dimensions: toggle(state.dimensions, id) })}
                  items={options.dimensions.map((d) => ({ id: d.column, label: d.label, title: d.table }))} />
                <div className="field">
                  <label htmlFor="build-range">Over</label>
                  <select id="build-range" value={state.timeRange} onChange={(e) => set({ timeRange: e.target.value })}>
                    <option value="">Any period</option>
                    {options.ranges.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="build-text">In your words</label>
                  <input id="build-text" className="dash-input" placeholder="Optional" value={state.text} onChange={(e) => set({ text: e.target.value })} />
                </div>
              </>
            )}
          </>
        )}

        {step === "review" && (
          <>
            <p className="page-note">Untick what you do not want, change the chart, or reword a new question.</p>
            <ul className="build-tiles">
              {state.tiles.map((t, i) => (
                <li key={`${t.source}-${t.ref || i}`}>
                  <input type="checkbox" aria-label={`Include: ${t.question}`} checked={t.ticked} onChange={(e) => set({ tiles: tick(state.tiles, i, e.target.checked) })} />
                  {canReword(t)
                    ? <input className="dash-input" aria-label="Question" value={t.question} onChange={(e) => set({ tiles: reword(state.tiles, i, e.target.value) })} />
                    : <span className="build-question">{t.question}</span>}
                  <select aria-label={`Chart for: ${t.question}`} value={t.chart} onChange={(e) => set({ tiles: setChart(state.tiles, i, e.target.value) })}>
                    {CHARTS.map((c) => <option key={c} value={c}>{CHART_NAMES[c]}</option>)}
                  </select>
                  <span className="dash-meta">{{ curated: "curated", saved: "saved", asked: "new" }[t.source]}</span>
                </li>
              ))}
            </ul>
            {state.tiles.length === 0 && <p className="quiet">Nothing matched. Go back and pick something else.</p>}
            <div className="field">
              <label htmlFor="build-title">Title</label>
              <input id="build-title" className="dash-input" placeholder="Optional" value={state.title} onChange={(e) => set({ title: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="build-workspace">Save in</label>
              <select id="build-workspace" value={into} disabled={!workspaces || workspaces.length === 0} onChange={(e) => set({ workspaceId: e.target.value })}>
                {(workspaces || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </div>
            {options && options.time_columns.length > 0 && (
              <div className="field">
                <label htmlFor="build-time">Date filter</label>
                <select id="build-time" value={state.timeColumn} onChange={(e) => set({ timeColumn: e.target.value })}>
                  <option value="">None</option>
                  {options.time_columns.map((c) => <option key={c.column} value={c.column}>{c.label}</option>)}
                </select>
              </div>
            )}
          </>
        )}

        {step === "build" && (
          <>
            {job && <JobProgress job={job} onCancel={running(job) ? () => cancelJob(job.id).catch((e) => setFault(describeBuilderError(e))) : undefined} />}
            {report && (
              <div className="build-report" role="status">
                <p>Built {report.report.built.length} of {report.report.built.length + report.report.skipped.length} charts.</p>
                <ul>{skippedLines(report.report.skipped).map((line) => <li key={line}>{line}</li>)}</ul>
                <button type="button" className="index-rebuild is-primary" onClick={() => onOpen(report.hash)}>Open the dashboard</button>
              </div>
            )}
          </>
        )}

        {fault && <p className="rows-error" role="alert">{fault}</p>}
        <div className="dash-row">
          {index > 0 && step !== "build" && <button type="button" className="index-rebuild" disabled={busy} onClick={() => go(["sources", "inputs", "review"][index - 1])}>Back</button>}
          {advance && <button type="button" className="index-rebuild is-primary" disabled={busy || !canAdvance(step, chosen)}
            onClick={() => go(step === "sources" ? "inputs" : "review")}>{step === "inputs" && busy ? "Finding questions" : "Next"}</button>}
          {step === "review" && <button type="button" className="index-rebuild is-primary" disabled={busy || !canAdvance("review", chosen) || !buildBody(chosen).tiles.length} onClick={build}>Build</button>}
          {step === "build" && (over || fault) && !running(job) && (
            <>
              <button type="button" className="index-rebuild is-primary" onClick={back}>Back to review</button>
              <button type="button" className="index-rebuild" onClick={restart}>Start over</button>
            </>
          )}
        </div>
      </div>
    </details>
  );
}
