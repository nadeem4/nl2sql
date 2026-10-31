import React, { useEffect, useReducer, useRef, useState } from "react";
import { barShares, firstNumericColumn, numericColumns } from "../lib/artifacts.js";
import {
  PAGE_SIZE, csvUrl, firstPage, initialView, pageNav, pageRange, resultTabs, rowCount, rowsView, truncatedNotice,
  viewUrl,
} from "./results.js";

// A run's saved results as a card: one tab per result when there are several,
// the total in the head (and a notice when the result was cut at its row
// ceiling), then one page of rows from the server at a time. The table fills
// the column, the header sticks, and the first numeric column carries an
// in-cell bar sized to the page's largest value. NULL stays dimmed. The CSV is
// the whole result, streamed by the server, never the rows on screen.

// `i` is the row, so the bars grow one after another (styles/artifacts.css, `--i`).
function Bar({ bar, i = 0 }) {
  if (!bar) return <span className="bar" aria-hidden="true" />;
  return (
    <span className={bar.negative ? "bar neg" : "bar"} aria-hidden="true">
      <i style={{ "--share": bar.share, "--i": i }} />
    </span>
  );
}

function Grid({ columns, rows, busy = false }) {
  const numeric = numericColumns(columns, rows);
  const barCol = firstNumericColumn(columns, rows);
  const bars = barCol < 0 ? [] : barShares(rows.map((r) => r[barCol]));
  // The number beside each bar gets one fixed width, so every bar starts and ends in line.
  const numWidth = Math.max(4, ...rows.map((r) => (barCol < 0 || r[barCol] === null ? 4 : String(r[barCol]).length)));
  return (
    <div className="art-scroll" tabIndex={0} role="region" aria-label="Result rows" aria-busy={busy || undefined}
      data-busy={busy || undefined}>
      <table className="rows-table" style={{ "--num-w": `${numWidth}ch` }}>
        <thead>
          <tr>{columns.map((c, j) => <th key={j} scope="col" className={numeric[j] ? "num" : undefined}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
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
          {!rows.length && (
            <tr><td className="rows-empty" colSpan={Math.max(1, columns.length)}>No rows came back.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// One result, a page at a time. The page on screen stays while the next one
// loads, so Next never blanks the card.
// `view` (results.js, `rowsView`) says which result and which offset; a new
// result remounts this, so no page of the last one is shown under its tab.
function PagedResult({ requestId, role, summary, subQueries, view, onPage }) {
  const empty = firstPage(summary);
  const [page, setPage] = useState(empty);
  const [loading, setLoading] = useState(!empty);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const resultId = summary.node_id;
  const offset = view.offset;

  useEffect(() => {
    if (empty) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(viewUrl(requestId, view, role, PAGE_SIZE), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          const reply = await response.json().catch(() => ({}));
          throw new Error(typeof reply.detail === "string" ? reply.detail : `The server answered ${response.status}.`);
        }
        return response.json();
      })
      .then((next) => {
        setPage(next);
        setLoading(false);
      })
      .catch((e) => {
        if (e.name === "AbortError") return;
        setError(e.message);
        setLoading(false);
      });
    return () => controller.abort();
  }, [requestId, role, resultId, offset, attempt]);

  const cut = truncatedNotice(summary, subQueries);
  const nav = page ? pageNav(page) : { prev: null, next: null };
  return (
    <>
      <div className="art-head">
        <span className="art-label">Result</span>
        <span className="art-meta" id="rows-total">{rowCount(summary)}</span>
        {summary.row_count > 0 && (
          <a className="inspect-close art-download" id="rows-csv" href={csvUrl(requestId, resultId, role)} download>
            Download CSV
          </a>
        )}
      </div>
      {cut && <p className="rows-cut" id="rows-truncated">{cut}</p>}
      {page ? (
        <Grid columns={page.columns || summary.columns || []} rows={page.rows || []} busy={loading} />
      ) : (
        !error && (
          <div className="rows-wait" aria-hidden="true">
            <span className="sk" style={{ width: "86%" }} />
            <span className="sk" style={{ width: "64%" }} />
            <span className="sk" style={{ width: "74%" }} />
          </div>
        )
      )}
      {error && (
        <p className="rows-error" role="alert">
          Could not load these rows. {error}{" "}
          <button type="button" className="linkish" onClick={() => setAttempt((a) => a + 1)}>Try again</button>
        </p>
      )}
      <div className="art-foot">
        <span id="rows-range" aria-live="polite">{loading && !page ? "Loading rows" : page ? pageRange(page, summary) : ""}</span>
        {(nav.prev !== null || nav.next !== null) && (
          <span className="rows-pager">
            <button type="button" className="inspect-close" id="rows-prev"
              disabled={nav.prev === null || loading} onClick={() => onPage(nav.prev)}>Previous</button>
            <button type="button" className="inspect-close" id="rows-next"
              disabled={nav.next === null || loading} onClick={() => onPage(nav.next)}>Next</button>
          </span>
        )}
      </div>
    </>
  );
}

// A run that saved no result (its manifest is empty) still has the sample the
// response carries: shown as it is, with no paging and no download.
export function SampleRows({ rows }) {
  const shown = rows.rows.length;
  return (
    <div className="art art-rows">
      <Grid columns={rows.columns} rows={rows.rows} />
      <div className="art-foot">
        <span>
          {rowCount({ row_count: rows.total_rows })}
          {shown < rows.total_rows && `, showing the first ${shown}`}
        </span>
      </div>
    </div>
  );
}

// The rows a run saved, read from `result_manifest`. A tab per result only
// when there is more than one; a tab opens on its result's first page. `role`
// is the role the run was asked as: the server serves an anonymous run's rows
// to that role only.
export default function RowsTable({ result, role }) {
  const manifest = result.result_manifest || [];
  const tabs = resultTabs(manifest);
  const [view, dispatch] = useReducer(rowsView, manifest, initialView);
  const tabRefs = useRef([]);
  const summary = manifest.find((r) => r.node_id === view.active) || manifest[0];
  const select = (id) => dispatch({ type: "select", id });
  const pick = (i) => {
    select(tabs[i].id);
    if (tabRefs.current[i]) tabRefs.current[i].focus();
  };
  // Arrow keys move along the tabs, as a tablist does.
  const onKey = (e, i) => {
    if (e.key === "ArrowRight") pick((i + 1) % tabs.length);
    else if (e.key === "ArrowLeft") pick((i - 1 + tabs.length) % tabs.length);
    else return;
    e.preventDefault();
  };
  return (
    <div className="art art-rows">
      {tabs.length > 0 && (
        <div className="seg rows-tabs" id="rows-tabs" role="tablist" aria-label="Results">
          {tabs.map((tab, i) => {
            const on = tab.id === summary.node_id;
            return (
              <button key={tab.id} type="button" role="tab" className="seg-opt" id={`rows-tab-${i}`}
                ref={(el) => { tabRefs.current[i] = el; }}
                aria-selected={on} aria-controls="rows-panel" tabIndex={on ? 0 : -1} title={tab.title}
                data-on={on ? "true" : undefined} onClick={() => select(tab.id)} onKeyDown={(e) => onKey(e, i)}>
                {tab.label}<span className="rows-tab-n">{tab.rows}</span>
              </button>
            );
          })}
        </div>
      )}
      <div id="rows-panel" role={tabs.length ? "tabpanel" : undefined}
        aria-labelledby={tabs.length ? `rows-tab-${tabs.findIndex((t) => t.id === summary.node_id)}` : undefined}>
        <PagedResult key={summary.node_id} requestId={result.trace_id} role={role} summary={summary}
          subQueries={result.sub_queries} view={{ ...view, active: summary.node_id }}
          onPage={(offset) => dispatch({ type: "page", offset })} />
      </div>
    </div>
  );
}
