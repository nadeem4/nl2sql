// A small SQL tokenizer for the SQL card's tint. Display only: it never decides
// anything about the query, and it returns plain text tokens that React renders
// as text, so whatever the SQL contains is never parsed as markup.
//
// kinds: "kw" (keyword), "fn" (a name called as a function), "num" (a number
// literal), "plain" (everything else, string literals and comments included).

const KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "AS", "ON", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER",
  "CROSS", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION", "ALL", "DISTINCT", "CASE", "WHEN",
  "THEN", "ELSE", "END", "IN", "IS", "NULL", "LIKE", "BETWEEN", "ASC", "DESC", "WITH", "TOP", "EXISTS", "OVER",
  "PARTITION", "INTERVAL", "FETCH", "NEXT", "ROWS", "ONLY", "TRUE", "FALSE", "USING", "EXCEPT", "INTERSECT",
]);

const IDENT = /[A-Za-z_][A-Za-z0-9_$]*/y;
const NUMBER = /(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/y;
const SPACE = /\s+/y;

function match(re, sql, i) {
  re.lastIndex = i;
  const m = re.exec(sql);
  return m ? m[0] : null;
}

// The end of a quoted run starting at i: 'a''b', "x", `x`, [x]. A doubled
// closing quote is an escaped one. Unterminated runs end with the input.
function quoted(sql, i) {
  const close = sql[i] === "[" ? "]" : sql[i];
  let j = i + 1;
  while (j < sql.length) {
    if (sql[j] === close) {
      if (sql[j + 1] === close && close !== "]") { j += 2; continue; }
      return j + 1;
    }
    j++;
  }
  return sql.length;
}

export function tokenizeSql(sql) {
  if (!sql) return [];
  const out = [];
  const push = (kind, text) => {
    const last = out[out.length - 1];
    if (kind === "plain" && last && last.kind === "plain") last.text += text;
    else out.push({ kind, text });
  };
  let prev = ""; // the last non-space character, to spot a name after a dot
  let i = 0;
  while (i < sql.length) {
    const ch = sql[i];
    let text;
    if (ch === "'" || ch === '"' || ch === "`" || ch === "[") {
      text = sql.slice(i, quoted(sql, i));
      push("plain", text);
    } else if (sql.startsWith("--", i)) {
      const end = sql.indexOf("\n", i);
      text = sql.slice(i, end < 0 ? sql.length : end);
      push("plain", text);
    } else if (sql.startsWith("/*", i)) {
      const end = sql.indexOf("*/", i + 2);
      text = sql.slice(i, end < 0 ? sql.length : end + 2);
      push("plain", text);
    } else if ((text = match(IDENT, sql, i))) {
      const word = text.toUpperCase();
      const after = match(SPACE, sql, i + text.length) || "";
      const called = sql[i + text.length + after.length] === "(";
      const kind = prev === "." ? "plain" : KEYWORDS.has(word) ? "kw" : called ? "fn" : "plain";
      push(kind, text);
    } else if ((text = match(NUMBER, sql, i))) {
      push("num", text);
    } else {
      text = ch;
      push("plain", text);
    }
    i += text.length;
    const trimmed = text.trim();
    if (trimmed) prev = trimmed[trimmed.length - 1];
  }
  return out;
}

// The tokens line by line, for a card that numbers its lines. A token that
// spans a break (a multi-line string) is split at it, keeping its kind.
export function sqlLines(sql) {
  if (!sql) return [];
  const lines = [[]];
  for (const token of tokenizeSql(sql)) {
    token.text.split("\n").forEach((part, n) => {
      if (n > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ kind: token.kind, text: part });
    });
  }
  return lines;
}
