import React, { useEffect, useRef, useState } from "react";
import { formatSql } from "./run.js";
import { sqlLines } from "./sqlHighlight.js";

// The generated SQL as a card: a header strip naming the plan it came from, a
// Copy button, numbered lines and a keyword tint. Every token is rendered as a
// text child, never as HTML, because the SQL can contain anything.

export function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="5" y="5" width="9" height="9" rx="2" />
      <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" />
    </svg>
  );
}

// The clipboard API needs a secure context and permission; where it is not
// there, select the text so the reader can copy it, and try the old command.
async function copyText(text, node) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    if (!node) return false;
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    }
  }
}

export default function SqlCard({ sql, plan }) {
  const text = formatSql(sql);
  const code = useRef(null);
  const [copied, setCopied] = useState(null);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(null), 1400);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = async () => setCopied((await copyText(text, code.current)) ? "Copied" : "Selected");
  return (
    <div className="art art-sql">
      <div className="art-head">
        <span className="art-label">Generated SQL</span>
        {plan && <span className="art-meta">from plan {plan}</span>}
        <button type="button" className="inspect-close art-copy" onClick={copy} aria-live="polite">
          <CopyIcon />{copied || "Copy"}
        </button>
      </div>
      <pre className="sql-code" ref={code} tabIndex={0} aria-label="Generated SQL">
        {sqlLines(text).map((line, i) => (
          <span className="sql-line" key={i}>
            {line.map((t, j) => (t.kind === "plain" ? t.text : <span key={j} className={`tk-${t.kind}`}>{t.text}</span>))}
            {"\n"}
          </span>
        ))}
      </pre>
    </div>
  );
}
