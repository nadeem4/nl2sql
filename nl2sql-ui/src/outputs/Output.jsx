import React from "react";
import { rendererFor } from "./output.js";

// A chat tool's `ToolOutput`, rendered by its kind. A stub for now: W2a replaces each branch with its renderer
// (Chart.jsx, Mermaid.jsx, Report.jsx); until then the body is shown as the text it is.
export default function Output({ output }) {
  const kind = rendererFor(output);
  if (!output) return null;
  const body = typeof output.body === "string" ? output.body : JSON.stringify(output.body, null, 2);
  return (
    <figure className="tool-output" data-kind={kind}>
      <figcaption className="art-label">{output.title}</figcaption>
      <pre className="art-meta">{body}</pre>
    </figure>
  );
}
