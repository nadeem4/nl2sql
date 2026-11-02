import React from "react";
import { saveFile } from "../api/outputs.js";
import { inline, parseMarkdown } from "./markdown.js";

// A report: the Markdown the report tool wrote, drawn as React elements (markdown.js parses the subset it uses, so
// nothing in the text can inject markup), and the same text as a .md download.

function Spans({ text }) {
  return inline(text).map((s, i) => {
    if (s.type === "strong") return <strong key={i}>{s.text}</strong>;
    if (s.type === "em") return <em key={i}>{s.text}</em>;
    if (s.type === "code") return <code key={i}>{s.text}</code>;
    if (s.type === "link") return <a key={i} href={s.href} target="_blank" rel="noreferrer noopener">{s.text}</a>;
    return <React.Fragment key={i}>{s.text}</React.Fragment>;
  });
}

function Blocks({ blocks }) {
  return blocks.map((b, i) => {
    if (b.type === "heading") {
      const Tag = `h${Math.min(6, b.level + 2)}`; // the card is already inside a page with its own h1 and h2
      return <Tag key={i}><Spans text={b.text} /></Tag>;
    }
    if (b.type === "paragraph") return <p key={i}><Spans text={b.text} /></p>;
    if (b.type === "list") {
      const Tag = b.ordered ? "ol" : "ul";
      return <Tag key={i}>{b.items.map((item, j) => <li key={j}><Spans text={item} /></li>)}</Tag>;
    }
    if (b.type === "code") return <pre key={i}><code>{b.text}</code></pre>;
    if (b.type === "table") {
      return (
        <div className="report-table" key={i} tabIndex={0} role="region" aria-label="Table">
          <table>
            <thead>
              <tr>{b.header.map((h, j) => <th key={j} scope="col" style={{ textAlign: b.align[j] || undefined }}><Spans text={h} /></th>)}</tr>
            </thead>
            <tbody>
              {b.rows.map((row, r) => (
                <tr key={r}>{row.map((cell, j) => <td key={j} style={{ textAlign: b.align[j] || undefined }}><Spans text={cell} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    if (b.type === "details") {
      return <details key={i}><summary>{b.summary}</summary><Blocks blocks={b.blocks} /></details>;
    }
    return null;
  });
}

const fileName = (title) => `${(title || "report").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "report"}.md`;

export default function Report({ source, title }) {
  const download = () => saveFile({ blob: new Blob([source], { type: "text/markdown" }), name: fileName(title) });
  return (
    <div className="art art-report">
      <div className="art-head">
        <span className="art-label">{title}</span>
        <span className="output-actions">
          <button type="button" className="inspect-close" onClick={download}>Download .md</button>
        </span>
      </div>
      <div className="report-body"><Blocks blocks={parseMarkdown(source)} /></div>
    </div>
  );
}
