// Which renderer shows a chat tool's `ToolOutput` (nl2sql/conversations/models.py): chart, mermaid, markdown or
// json. A kind the page does not know is shown as plain text rather than dropped.
const KINDS = ["chart", "mermaid", "markdown", "json"];

export function rendererFor(output) {
  return output && KINDS.includes(output.kind) ? output.kind : "text";
}

// The ChartSpec a chart output carries: the object, or its JSON text. Anything without a kind and the data the
// chart is drawn from is null, and the page shows the body as text instead.
export function chartSpecOf(output) {
  let body = output && output.body;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch {
      return null;
    }
  }
  return body && typeof body === "object" && body.kind && body.data && (Array.isArray(body.data.rows) || needsRows(body)) ? body : null;
}

// Saved chart outputs keep the spec but not its rows (the rows live in the saved result). Such a spec is fetched again
// from /results/.../chart, and the stored title (the question) is kept over the engine's default.
export function needsRows(spec) {
  const d = spec && spec.data;
  return Boolean(d && d.trace_id && d.result_id && !(Array.isArray(d.rows) && d.rows.length));
}

export function mergeFetched(stored, fresh) {
  return { ...fresh, title: stored.title || fresh.title };
}
