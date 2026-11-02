import React from "react";
import Chart from "../outputs/Chart.jsx";
import { cellStyle } from "./layout.js";
import { ageLabel, tileState } from "./tile.js";
import TileEdit from "./Edit.jsx";

// One chart of a dashboard: its title, how old its result is, and the result as its status allows. A chart that
// cannot be shown says why in a sentence, never a blank card. `edit` is Edit.jsx's set of actions, present only
// while the dashboard is being edited.
function Body({ state, tile, busy, onRefresh, onRemove }) {
  if (state.kind === "chart") return <Chart spec={state.spec} />;
  return (
    <div className="dash-note" data-kind={state.kind} role={state.kind === "error" ? "alert" : undefined}>
      <p>{state.message}</p>
      {state.kind === "approval" && (
        <p className="dash-note-sub">Someone with the right role grants it in <a href="#/approvals">Approvals</a>, then run it again.</p>
      )}
      {state.kind === "approval" && (
        <button type="button" className="index-rebuild" disabled={busy} onClick={onRefresh}>Run again</button>
      )}
      {state.removable && onRemove && (
        <button type="button" className="index-rebuild" disabled={busy} onClick={onRemove}>Remove this chart</button>
      )}
      {state.kind === "pending" && (
        <button type="button" className="index-rebuild" disabled={busy} onClick={onRefresh}>Run now</button>
      )}
    </div>
  );
}

export default function Tile({ tile, run, place, busy, now, edit, onRefresh, onRemove }) {
  const state = tileState(run);
  const age = run ? ageLabel(run.at, now) : "";
  return (
    <section className="dash-tile" style={cellStyle(place)} aria-label={tile.title} aria-busy={busy || undefined}>
      <header className="dash-tile-head">
        <h3>{tile.title}</h3>
        {age && <span className="dash-age" title={run.at}>{busy ? "Refreshing" : age}</span>}
      </header>
      {edit && <TileEdit tile={tile} busy={busy} {...edit} />}
      <Body state={state} tile={tile} busy={busy} onRefresh={onRefresh} onRemove={onRemove} />
    </section>
  );
}
