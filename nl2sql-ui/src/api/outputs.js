// A saved result and what is drawn from it (wave_d.md, "Charts, diagrams and reports"): its rows a page at a time, its
// CSV, and its chart. A run's rows are read for the caller only; the server answers another caller's run as not found.
import { download, request } from "./http.js";

const enc = encodeURIComponent;

// The engine's ChartSpec for a result. `kind` forces one of line, bar, big_number or table; without it the engine
// chooses from the column kinds.
export function getChart(traceId, resultId, kind) {
  if (kind) return request("GET", `/results/${enc(traceId)}/${enc(resultId)}/chart?kind=${enc(kind)}`);
  return request("GET", `/results/${enc(traceId)}/${enc(resultId)}/chart`);
}

// Rows `offset` to `offset + limit`; `next_offset` in the answer is where the next page starts, null at the end.
export function getResultPage(traceId, resultId, offset = 0, limit = 100) {
  return request("GET", `/results/${enc(traceId)}/${enc(resultId)}?offset=${offset}&limit=${limit}`);
}

// The whole result as CSV, streamed by the server. Resolves with `{blob, name}` for saveFile.
export async function downloadCsv(traceId, resultId) {
  return { blob: await download(`/results/${enc(traceId)}/${enc(resultId)}.csv`), name: `${resultId}.csv` };
}

// The chart as a Vega-Lite v5 document (a table has none: the server answers 422).
export async function downloadVegaLite(traceId, resultId, kind) {
  const query = kind ? `&kind=${enc(kind)}` : "";
  const blob = await download(`/results/${enc(traceId)}/${enc(resultId)}/chart?format=vega-lite${query}`);
  return { blob, name: `${resultId}-vega-lite.json` };
}

// Hands a downloaded file to the browser's save dialog.
export function saveFile({ blob, name }) {
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000); // revoking at once can cancel the download in some browsers
}
