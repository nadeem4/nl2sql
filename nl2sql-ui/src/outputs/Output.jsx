import React from "react";
import Chart from "./Chart.jsx";
import Mermaid from "./Mermaid.jsx";
import Report from "./Report.jsx";
import { chartSpecOf, rendererFor } from "./output.js";

// A chat tool's `ToolOutput`, rendered by its kind: a chart from its ChartSpec, a diagram from its Mermaid text, a
// report from its Markdown. A body the page cannot read, or a kind it does not know, is shown as the text it is
// rather than dropped.
function Plain({ output, kind }) {
  const body = typeof output.body === "string" ? output.body : JSON.stringify(output.body, null, 2);
  return (
    <div className="art">
      <div className="art-head"><span className="art-label">{output.title}</span></div>
      <pre className="art-meta report-body" data-kind={kind}>{body}</pre>
    </div>
  );
}

export default function Output({ output }) {
  if (!output) return null;
  const kind = rendererFor(output);
  const spec = kind === "chart" ? chartSpecOf(output) : null;
  let body = <Plain output={output} kind={kind} />;
  if (spec) body = <Chart spec={spec} />;
  else if (kind === "mermaid" && typeof output.body === "string") body = <Mermaid source={output.body} title={output.title} />;
  else if (kind === "markdown" && typeof output.body === "string") body = <Report source={output.body} title={output.title} />;
  return <figure className="tool-output" data-kind={kind}>{body}</figure>;
}
