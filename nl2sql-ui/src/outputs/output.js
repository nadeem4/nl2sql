// Which renderer shows a chat tool's `ToolOutput` (nl2sql/conversations/models.py): chart, mermaid, markdown or
// json. W2a fills the renderers; a kind the page does not know is shown as plain text rather than dropped.
const KINDS = ["chart", "mermaid", "markdown", "json"];

export function rendererFor(output) {
  return output && KINDS.includes(output.kind) ? output.kind : "text";
}
