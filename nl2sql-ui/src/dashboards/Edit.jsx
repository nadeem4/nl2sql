import React, { useState } from "react";

// What a person can change on a chart without a model: its title, its chart type, its place and size, and whether it
// stays. Changing what data a chart shows is chat's job, so none of that is here.
const CHARTS = [["auto", "Automatic"], ["line", "Line"], ["bar", "Bar"], ["big_number", "Big number"], ["table", "Table"]];

export default function TileEdit({ tile, busy, first, last, onTitle, onChart, onShift, onSize, onRemove }) {
  const [title, setTitle] = useState(tile.title);
  const save = () => {
    const next = title.trim();
    if (next && next !== tile.title) onTitle(next);
    else setTitle(tile.title);
  };
  return (
    <div className="dash-edit">
      <div className="field">
        <label htmlFor={`tt-${tile.id}`}>Title</label>
        <input id={`tt-${tile.id}`} className="dash-input" value={title} disabled={busy}
          onChange={(e) => setTitle(e.target.value)} onBlur={save}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      </div>
      <div className="field">
        <label htmlFor={`tc-${tile.id}`}>Chart</label>
        <select id={`tc-${tile.id}`} value={tile.chart} disabled={busy} onChange={(e) => onChart(e.target.value)}>
          {CHARTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      <div className="dash-edit-row" role="group" aria-label="Place and size">
        <button type="button" className="inspect-close" disabled={busy || first} onClick={() => onShift(-1)}>Earlier</button>
        <button type="button" className="inspect-close" disabled={busy || last} onClick={() => onShift(1)}>Later</button>
        <button type="button" className="inspect-close" disabled={busy || tile.layout.w <= 1} onClick={() => onSize(-1, 0)}>Narrower</button>
        <button type="button" className="inspect-close" disabled={busy || tile.layout.w >= 4} onClick={() => onSize(1, 0)}>Wider</button>
        <button type="button" className="inspect-close" disabled={busy || tile.layout.h <= 1} onClick={() => onSize(0, -1)}>Shorter</button>
        <button type="button" className="inspect-close" disabled={busy} onClick={() => onSize(0, 1)}>Taller</button>
        <button type="button" className="inspect-close dash-danger" disabled={busy} onClick={onRemove}>Remove</button>
      </div>
    </div>
  );
}
