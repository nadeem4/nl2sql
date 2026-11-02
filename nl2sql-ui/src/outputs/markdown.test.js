// Run with `npm test`. The report renderer reads the small subset of Markdown the report tool writes (headings,
// paragraphs, lists, tables, <details>, fenced code, bold, italic, inline code and links) into blocks the page can
// draw without ever putting raw HTML on it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { inline, parseMarkdown } from "./markdown.js";

test("headings, paragraphs and lists", () => {
  const blocks = parseMarkdown("# Title\n\nSome text\nover two lines.\n\n## Part\n\n- one\n- two\n\n1. first\n2. second");
  assert.deepEqual(blocks, [
    { type: "heading", level: 1, text: "Title" },
    { type: "paragraph", text: "Some text over two lines." },
    { type: "heading", level: 2, text: "Part" },
    { type: "list", ordered: false, items: ["one", "two"] },
    { type: "list", ordered: true, items: ["first", "second"] },
  ]);
});

test("a pipe table has a header, alignment and rows, and no divider row", () => {
  const [t] = parseMarkdown("| genre | tracks |\n|---|---:|\n| Rock | 1297 |\n| Jazz | 130 |");
  assert.deepEqual(t, {
    type: "table", header: ["genre", "tracks"], align: [null, "right"], rows: [["Rock", "1297"], ["Jazz", "130"]],
  });
});

test("fenced code keeps its text, blank lines and language", () => {
  const [c] = parseMarkdown("```sql\nSELECT 1;\n\nSELECT 2;\n```");
  assert.deepEqual(c, { type: "code", lang: "sql", text: "SELECT 1;\n\nSELECT 2;" });
});

test("details has a summary and parsed contents", () => {
  const [d] = parseMarkdown("<details>\n<summary>The SQL</summary>\n\n```sql\nSELECT 1\n```\n\n</details>");
  assert.equal(d.type, "details");
  assert.equal(d.summary, "The SQL");
  assert.deepEqual(d.blocks, [{ type: "code", lang: "sql", text: "SELECT 1" }]);
});

test("inline: bold, italic, code and links become spans", () => {
  assert.deepEqual(inline("a **b** *c* `d` [e](https://x.test/y)"), [
    { type: "text", text: "a " }, { type: "strong", text: "b" }, { type: "text", text: " " },
    { type: "em", text: "c" }, { type: "text", text: " " }, { type: "code", text: "d" }, { type: "text", text: " " },
    { type: "link", text: "e", href: "https://x.test/y" },
  ]);
});

test("a link that is not http(s) is plain text, and raw HTML is never passed through", () => {
  assert.deepEqual(inline("[x](javascript:alert(1))"), [{ type: "text", text: "[x](javascript:alert(1))" }]);
  assert.deepEqual(inline("<img src=x onerror=1>"), [{ type: "text", text: "<img src=x onerror=1>" }]);
  const [p] = parseMarkdown("<script>alert(1)</script>");
  assert.equal(p.type, "paragraph");
});

test("an empty report has no blocks", () => {
  assert.deepEqual(parseMarkdown(""), []);
  assert.deepEqual(parseMarkdown(null), []);
});

test("the engine's report shape: details with the summary on one line, then a fenced block", () => {
  const src = "## Q\n\nanswer\n\n<details><summary>SQL</summary>\n\n```sql\nSELECT 1\n```\n\n</details>\n\nTrace: t1";
  const blocks = parseMarkdown(src);
  assert.deepEqual(blocks.map((b) => b.type), ["heading", "paragraph", "details", "paragraph"]);
  assert.equal(blocks[2].summary, "SQL");
  assert.deepEqual(blocks[2].blocks, [{ type: "code", lang: "sql", text: "SELECT 1" }]);
});

test("fences accept info text and longer runs of backticks", () => {
  assert.deepEqual(parseMarkdown("```c++\nx\n```"), [{ type: "code", lang: "c++", text: "x" }]);
  assert.deepEqual(parseMarkdown("```sql title\nx\n```"), [{ type: "code", lang: "sql", text: "x" }]);
  assert.deepEqual(parseMarkdown("````\n```\ninner\n```\n````"), [{ type: "code", lang: "", text: "```\ninner\n```" }]);
});

test("every line is consumed: odd lines never hang the parser", () => {
  for (const src of ["<details>\n", "<details open>\nx", "```", "<details><summary>a</summary>", "#nospace", "  ```"]) {
    assert.ok(Array.isArray(parseMarkdown(src)), src);
  }
});

test("an escaped pipe stays inside its table cell", () => {
  const [t] = parseMarkdown("| a | b |\n|---|---|\n| x \\| y | 2 |");
  assert.deepEqual(t.rows, [["x | y", "2"]]);
});
