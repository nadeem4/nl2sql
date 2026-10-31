// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { sqlLines, tokenizeSql } from "./sqlHighlight.js";

const tinted = (sql) => tokenizeSql(sql).filter((t) => t.kind !== "plain").map((t) => [t.kind, t.text]);

test("tokenizeSql loses no character: the tokens join back to the input", () => {
  const sql = "SELECT g.Name AS genre, SUM(il.Quantity) AS n\nFROM Genre AS g WHERE x = 'a''b' -- note\nLIMIT 10";
  assert.equal(tokenizeSql(sql).map((t) => t.text).join(""), sql);
});

test("keywords, function names and numbers are tinted, case-insensitively", () => {
  assert.deepEqual(tinted("select count(t.Id), Sum (x) from T limit 10"), [
    ["kw", "select"], ["fn", "count"], ["fn", "Sum"], ["kw", "from"], ["kw", "limit"], ["num", "10"],
  ]);
});

test("a keyword followed by a parenthesis stays a keyword", () => {
  assert.deepEqual(tinted("WHERE a IN (1, 2.5)"), [["kw", "WHERE"], ["kw", "IN"], ["num", "1"], ["num", "2.5"]]);
});

test("nothing inside a string literal or a quoted identifier is tinted", () => {
  assert.deepEqual(tinted("SELECT 'FROM 10 COUNT(' AS \"ORDER\", [SELECT] FROM `LIMIT`"), [
    ["kw", "SELECT"], ["kw", "AS"], ["kw", "FROM"],
  ]);
  const toks = tokenizeSql("x = 'it''s'");
  assert.ok(toks.some((t) => t.kind === "plain" && t.text.includes("'it''s'")));
});

test("digits inside a name are not a number, and a name after a dot is never a keyword", () => {
  assert.deepEqual(tinted("SELECT t1.Order, t2.count FROM t1"), [["kw", "SELECT"], ["kw", "FROM"]]);
});

test("comments are not tinted", () => {
  assert.deepEqual(tinted("-- SELECT 1\n/* FROM 2 */ SELECT"), [["kw", "SELECT"]]);
});

test("an unterminated string runs to the end without throwing", () => {
  assert.deepEqual(tokenizeSql("SELECT 'abc").map((t) => t.text).join(""), "SELECT 'abc");
});

test("tokenizeSql returns nothing for empty input", () => {
  assert.deepEqual(tokenizeSql(""), []);
  assert.deepEqual(tokenizeSql(null), []);
});

test("markup in the SQL comes back as plain text, never as markup", () => {
  const sql = "SELECT '<img src=x onerror=alert(1)>' AS x";
  const toks = tokenizeSql(sql);
  assert.ok(toks.every((t) => typeof t.text === "string"));
  assert.ok(toks.some((t) => t.kind === "plain" && t.text.includes("'<img src=x onerror=alert(1)>'")));
});

test("sqlLines splits tokens at line breaks, one array per line", () => {
  const lines = sqlLines("SELECT a\nFROM 'x\ny'\nLIMIT 5");
  assert.equal(lines.length, 4);
  assert.deepEqual(lines.map((l) => l.map((t) => t.text).join("")), ["SELECT a", "FROM 'x", "y'", "LIMIT 5"]);
  assert.equal(lines[3][0].kind, "kw");
  assert.equal(lines[3][2].kind, "num");
});

test("sqlLines of empty input is no lines", () => {
  assert.deepEqual(sqlLines(""), []);
});
