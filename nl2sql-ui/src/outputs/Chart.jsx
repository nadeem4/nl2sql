import React, { useCallback, useRef, useState } from "react";
import { downloadVegaLite, saveFile } from "../api/outputs.js";
import { chartModel, truncatedNote } from "./chart.js";
import { SampleRows } from "./RowsTable.jsx";

// A ChartSpec as an SVG card: the engine chose the kind and the encodings, chart.js places them, this draws them in
// the page's own tokens (styles/outputs.css). It is sized to its container, so it redraws at a new width instead of
// scaling its text. A table spec, or the "Table" switch, shows the rows the chart was drawn from.

// A callback ref on the card, which stays mounted when the Table switch swaps the body, so the width is always tracked.
function useWidth() {
  const watch = useRef(null);
  const [width, setWidth] = useState(640);
  const ref = useCallback((el) => {
    if (watch.current) watch.current.disconnect();
    watch.current = null;
    if (!el || typeof ResizeObserver === "undefined") return;
    watch.current = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))));
    watch.current.observe(el);
  }, []);
  return [ref, width];
}

function Legend({ items }) {
  if (!items.length) return null;
  return (
    <ul className="chart-legend" aria-label="Series">
      {items.map((item) => <li key={item.name} className={`s${item.slot}`}><i aria-hidden="true" /><span>{item.name}</span></li>)}
    </ul>
  );
}

function Lines({ model }) {
  const { plot } = model;
  return (
    <svg className="chart-svg" viewBox={`0 0 ${model.width} ${model.height}`} aria-hidden="true">
      {model.yTicks.map((t) => (
        <g key={t.y}>
          <line className="chart-grid" x1={plot.x0} x2={plot.x1} y1={t.y} y2={t.y} />
          <text x={plot.x0 - 8} y={t.y} textAnchor="end" dominantBaseline="middle">{t.label}</text>
        </g>
      ))}
      <line className="chart-axis" x1={plot.x0} x2={plot.x1} y1={plot.y1} y2={plot.y1} />
      {model.xTicks.map((t) => (
        <text key={t.x} x={t.x} y={plot.y1 + 18} textAnchor="middle">{t.label}</text>
      ))}
      {model.lines.map((line) => (
        <g key={line.name} className={`s${line.slot}`}>
          <path className="chart-line" d={line.d} />
          {line.points.map((p, n) => (
            <circle key={n} className="chart-dot" cx={p.x} cy={p.y} r={4}>
              <title>{`${line.name}, ${p.label}: ${p.full}`}</title>
            </circle>
          ))}
        </g>
      ))}
    </svg>
  );
}

function Bars({ model }) {
  const { plot } = model;
  return (
    <svg className="chart-svg" viewBox={`0 0 ${model.width} ${model.height}`} aria-hidden="true">
      {model.ticks.map((t) => (
        <g key={t.x}>
          <line className="chart-grid" x1={t.x} x2={t.x} y1={plot.y0} y2={plot.y1} />
          <text x={t.x} y={plot.y1 + 18} textAnchor="middle">{t.label}</text>
        </g>
      ))}
      <line className="chart-axis" x1={model.zeroX} x2={model.zeroX} y1={plot.y0} y2={plot.y1} />
      {model.groups.map((group, row) => (
        <g key={row}>
          <text className="cat" x={plot.x0 - 8} y={group.bars[0].y + (group.bars.at(-1).y + group.bars.at(-1).h - group.bars[0].y) / 2}
            textAnchor="end" dominantBaseline="middle">
            {group.label}<title>{group.full}</title>
          </text>
          {group.bars.map((bar, k) => (
            <g key={k} className={`s${bar.slot}`}>
              <rect className="chart-bar" rx={4} x={bar.x} y={bar.y} width={bar.w} height={bar.h}>
                <title>{`${group.full}, ${bar.name}: ${bar.full}`}</title>
              </rect>
              <text className="val" x={bar.value < 0 ? bar.x - 6 : bar.x + bar.w + 6} y={bar.y + bar.h / 2}
                textAnchor={bar.value < 0 ? "end" : "start"} dominantBaseline="middle">{bar.text}</text>
            </g>
          ))}
        </g>
      ))}
    </svg>
  );
}

export default function Chart({ spec }) {
  const [box, width] = useWidth();
  const [asTable, setAsTable] = useState(spec.kind === "table");
  const [error, setError] = useState(null);
  const model = asTable ? null : chartModel(spec, { width });
  const note = truncatedNote(spec);
  const { trace_id: traceId, result_id: resultId } = spec.data;
  const label = [spec.title, spec.takeaway].filter(Boolean).join(". ");

  const exportVega = () => downloadVegaLite(traceId, resultId, spec.kind).then(saveFile).catch((e) => setError(e.message));

  return (
    <div className="art art-chart" ref={box}>
      <div className="art-head">
        <span className="art-label">{spec.title}</span>
        <span className="output-actions">
          {spec.kind !== "table" && (
            <button type="button" className="inspect-close" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
              {asTable ? "Chart" : "Table"}
            </button>
          )}
          {spec.kind !== "table" && (
            <button type="button" className="inspect-close" onClick={exportVega}>Download Vega-Lite</button>
          )}
        </span>
      </div>
      {model ? (
        <div className="chart-body" role="img" aria-label={label}>
          {model.kind === "big_number" && (
            <p className="chart-big"><strong>{model.text}</strong><span title={model.full}>{model.label}</span></p>
          )}
          {model.kind === "line" && <Lines model={model} />}
          {model.kind === "bar" && <Bars model={model} />}
          {model.legend && <Legend items={model.legend} />}
        </div>
      ) : (
        <SampleRows rows={{ columns: spec.data.columns, rows: spec.data.rows, total_rows: spec.data.rows.length }} />
      )}
      {spec.takeaway && !asTable && <p className="chart-takeaway">{spec.takeaway}</p>}
      {note && <p className="rows-cut">{note}</p>}
      {error && <p className="rows-error" role="alert">Could not export the chart. {error}</p>}
      <div className="art-foot"><span className="chart-reason">{spec.reason}</span></div>
    </div>
  );
}
