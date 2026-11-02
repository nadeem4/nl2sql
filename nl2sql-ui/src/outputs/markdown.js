// The subset of Markdown Wave C's report tool writes, as plain blocks: headings, paragraphs, lists, pipe tables,
// <details> with a <summary>, fenced code, and inline bold, italic, code and links. Raw HTML is never passed through:
// Report.jsx draws these blocks as React elements, so nothing in a report can inject markup.

export function inline(text) {
  const out = [];
  const pattern = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  const plain = (s) => {
    if (s) out.push({ type: "text", text: s });
  };
  for (const m of text.matchAll(pattern)) {
    if (m[4] !== undefined && !/^https?:\/\//.test(m[5])) continue; // an unsafe link stays as it was written
    plain(text.slice(last, m.index));
    if (m[1] !== undefined) out.push({ type: "strong", text: m[1] });
    else if (m[2] !== undefined) out.push({ type: "em", text: m[2] });
    else if (m[3] !== undefined) out.push({ type: "code", text: m[3] });
    else out.push({ type: "link", text: m[4], href: m[5] });
    last = m.index + m[0].length;
  }
  plain(text.slice(last));
  return out;
}

const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const isDivider = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const alignOf = (cell) => (cell.endsWith(":") ? (cell.startsWith(":") ? "center" : "right") : cell.startsWith(":") ? "left" : null);

export function parseMarkdown(source) {
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
      i += 1;
      blocks.push({ type: "code", lang: fence[1], text: body.join("\n") });
      continue;
    }
    if (/^<details>\s*$/.test(line.trim())) {
      const body = [];
      i += 1;
      let depth = 1;
      while (i < lines.length) {
        if (/^<details>\s*$/.test(lines[i].trim())) depth += 1;
        if (/^<\/details>\s*$/.test(lines[i].trim()) && --depth === 0) break;
        body.push(lines[i++]);
      }
      i += 1;
      const first = body.findIndex((l) => l.trim());
      const summary = first >= 0 ? body[first].match(/^\s*<summary>(.*)<\/summary>\s*$/) : null;
      if (summary) body.splice(first, 1);
      blocks.push({ type: "details", summary: summary ? summary[1] : "Details", blocks: parseMarkdown(body.join("\n")) });
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const header = cells(line);
      const align = cells(lines[i + 1]).map(alignOf);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push({ type: "table", header, align, rows });
      continue;
    }
    const item = line.match(/^\s*([-*]|\d+\.)\s+(.*)$/);
    if (item) {
      const ordered = /\d/.test(item[1]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*]|\d+\.)\s+(.*)$/);
        if (!m || /\d/.test(m[1]) !== ordered) break;
        items.push(m[2].trim());
        i += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    const text = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|<details>)/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
      text.push(lines[i++].trim());
    }
    blocks.push({ type: "paragraph", text: text.join(" ") });
  }
  return blocks;
}
