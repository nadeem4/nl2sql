import React, { useEffect, useState } from "react";
import { mermaidConfig } from "./mermaid.js";

// A Mermaid diagram, drawn by the library on first use: `import("mermaid")` is its own chunk, so a page with no
// diagram never downloads it. Mermaid has no colour, so the page's theme decides (mermaid.js).

const QUERY = "(prefers-color-scheme: dark)";

function usePrefersDark() {
  const [dark, setDark] = useState(() => typeof matchMedia === "function" && matchMedia(QUERY).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return undefined;
    const media = matchMedia(QUERY);
    const on = () => setDark(media.matches);
    media.addEventListener("change", on);
    return () => media.removeEventListener("change", on);
  }, []);
  return dark;
}

let drawn = 0;

export default function Mermaid({ source, title }) {
  const dark = usePrefersDark();
  const [svg, setSvg] = useState(null);
  const [error, setError] = useState(null);
  const [showSource, setShowSource] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    import("mermaid")
      .then(async ({ default: mermaid }) => {
        mermaid.initialize(mermaidConfig(dark));
        drawn += 1;
        return mermaid.render(`diagram-${drawn}`, source);
      })
      .then((out) => {
        if (!cancelled) setSvg(out.svg);
      })
      .catch((e) => {
        if (!cancelled) setError(e && e.message ? e.message : "The diagram could not be drawn.");
      });
    return () => {
      cancelled = true;
    };
  }, [source, dark]);

  return (
    <div className="art art-diagram">
      <div className="art-head">
        <span className="art-label">{title}</span>
        <span className="output-actions">
          <button type="button" className="inspect-close" aria-pressed={showSource} onClick={() => setShowSource(!showSource)}>
            Source
          </button>
        </span>
      </div>
      {error ? (
        <p className="output-error" role="alert">The diagram could not be drawn. {error}</p>
      ) : svg ? (
        <div className="mermaid-body" role="img" aria-label={title} dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <p className="mermaid-wait" aria-live="polite">Drawing the diagram</p>
      )}
      {showSource && <pre className="mermaid-source">{source}</pre>}
    </div>
  );
}
