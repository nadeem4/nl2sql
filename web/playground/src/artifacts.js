// Pure helpers for the run's data artifacts: the rows table's magnitude bars,
// its CSV, and the bars in the ledger and the retrieval pool. No React here, so
// `npm test` covers them with node's own test runner.

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

// A column is numeric when its first non-null value is a number.
export function numericColumns(columns, rows) {
  return (columns || []).map((_, j) => {
    const first = (rows || []).find((r) => r[j] !== null && r[j] !== undefined);
    return !!first && typeof first[j] === "number";
  });
}

export function firstNumericColumn(columns, rows) {
  return numericColumns(columns, rows).indexOf(true);
}

// Each value as a share of the column's largest magnitude, so the longest bar
// is the biggest number. A negative is measured by its size and marked, so it
// can be drawn differently instead of vanishing. Not a number: no bar.
export function barShares(values) {
  const max = Math.max(0, ...(values || []).filter(isNum).map(Math.abs));
  return (values || []).map((v) =>
    isNum(v) ? { share: max ? Math.abs(v) / max : 0, negative: v < 0 } : null
  );
}

// Each value placed within the set's own range, min 0 and max 1: similarity
// scores bunch together (0.36 to 0.40), so a bar against 0..1 would make every
// entry look the same. All equal: every bar full.
export function minMaxShares(values) {
  const nums = (values || []).filter(isNum);
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  return (values || []).map((v) => (isNum(v) ? (hi > lo ? (v - lo) / (hi - lo) : 1) : null));
}

// RFC 4180 CSV. Text a spreadsheet would run as a formula gets a leading
// apostrophe; numbers are left as they are.
function csvField(value) {
  if (value === null || value === undefined) return "";
  let text = typeof value === "object" ? JSON.stringify(value) : String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(columns, rows) {
  const line = (cells) => cells.map(csvField).join(",") + "\r\n";
  return line(columns || []) + (rows || []).map(line).join("");
}

export function csvFileName(result) {
  const id = result && result.trace_id;
  return id ? `nl2sql-rows-${id}.csv` : "nl2sql-rows.csv";
}
