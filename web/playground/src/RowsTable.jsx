import React from "react";
import { barShares, csvFileName, firstNumericColumn, numericColumns, toCsv } from "./artifacts.js";

// The result rows as a card: the table fills the column, the header sticks,
// and the first numeric column carries an in-cell bar so the answer's "more
// than twice" is visible in the data. NULL stays dimmed. The foot counts the
// rows and offers them as CSV, built in the page from the rows it already has.

// `i` is the row, so the bars grow one after another (styles.css, `--i`).
function Bar({ bar, i = 0 }) {
  if (!bar) return <span className="bar" aria-hidden="true" />;
  return (
    <span className={bar.negative ? "bar neg" : "bar"} aria-hidden="true">
      <i style={{ "--share": bar.share, "--i": i }} />
    </span>
  );
}

function download(rows, result) {
  const blob = new Blob([toCsv(rows.columns, rows.rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = csvFileName(result);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function RowsTable({ rows, result }) {
  const numeric = numericColumns(rows.columns, rows.rows);
  const barCol = firstNumericColumn(rows.columns, rows.rows);
  const bars = barCol < 0 ? [] : barShares(rows.rows.map((r) => r[barCol]));
  const shown = rows.rows.length;
  // The number beside each bar gets one fixed width, so every bar starts and ends in line.
  const numWidth = Math.max(4, ...rows.rows.map((r) => (barCol < 0 || r[barCol] === null ? 4 : String(r[barCol]).length)));
  return (
    <div className="art art-rows">
      <div className="art-scroll" tabIndex={0} role="region" aria-label="Result rows">
        <table className="rows-table" style={{ "--num-w": `${numWidth}ch` }}>
          <thead>
            <tr>{rows.columns.map((c, j) => <th key={j} scope="col" className={numeric[j] ? "num" : undefined}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => {
                  const shownCell = cell === null ? "NULL" : String(cell);
                  const cls = cell === null ? "null" : typeof cell === "number" ? "num" : undefined;
                  if (j === barCol) {
                    return (
                      <td key={j} className={`num barcell-td${cell === null ? " null" : ""}`}>
                        <span className="barcell"><Bar bar={bars[i]} i={i} /><span>{shownCell}</span></span>
                      </td>
                    );
                  }
                  return <td key={j} className={cls}>{shownCell}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="art-foot">
        <span>
          {rows.total_rows.toLocaleString()} {rows.total_rows === 1 ? "row" : "rows"}
          {shown < rows.total_rows && `, showing the first ${shown}`}
        </span>
        {shown > 0 && (
          <button type="button" className="inspect-close art-download" onClick={() => download(rows, result)}>
            Download CSV
          </button>
        )}
      </div>
    </div>
  );
}
