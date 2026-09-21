// Pure helpers over one `/api/ask` response. No React here, so `npm test`
// covers them with node's own test runner.

const CLAUSES = ["FROM", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN", "CROSS JOIN", "JOIN",
  "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT", "OFFSET", "UNION"];

// Display only: puts each top-level clause on its own line. The engine's SQL is
// shown verbatim otherwise; this never runs anywhere but the SQL pane.
export function formatSql(sql) {
  if (!sql) return "";
  let out = "";
  let quote = null;
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i];
    if (quote) {
      out += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      out += ch;
      continue;
    }
    if (ch === " ") {
      const rest = sql.slice(i + 1).toUpperCase();
      const clause = CLAUSES.find((c) => rest.startsWith(c) && !/[A-Z0-9_]/.test(rest[c.length] || ""));
      // "INNER JOIN" must not also break between INNER and JOIN.
      const inJoinPrefix = /(INNER|LEFT|RIGHT|FULL|CROSS)$/i.test(out);
      if (clause && !(clause === "JOIN" && inJoinPrefix)) {
        out += "\n";
        continue;
      }
    }
    out += ch;
  }
  return out;
}

// "Role 'viewer' denied access to 'chinook.Customer'." -> "Customer"
export function deniedTables(errors) {
  const names = [];
  for (const e of errors || []) {
    const m = /denied access to '([^']+)'/.exec(e.message || "");
    if (m) {
      const name = m[1].split(".").pop();
      if (!names.includes(name)) names.push(name);
    }
  }
  return names;
}

export function planTables(plan) {
  return ((plan && plan.tables) || []).map((t) => t.name);
}

// The SQL agent is a subgraph: its wall-clock time includes these nodes, which
// report their own time too. Nesting them keeps the column from reading as a sum.
const SQL_AGENT_NODES = new Set([
  "ast_planner", "logical_validator", "retry_handler", "refiner", "generator", "executor",
]);

// One row per node that ran, in the order the graph reported them (``timings``
// keeps insertion order). Rows carry the node name so a later per-node view can
// attach to them.
export function nodeLedger(usage, timings) {
  const nodes = (usage && usage.nodes) || {};
  const times = timings || {};
  const order = Object.keys(times).filter((n) => n !== "LangGraph");
  for (const n of Object.keys(nodes)) if (!order.includes(n)) order.push(n);

  const row = (name, depth) => {
    const u = nodes[name] || null;
    return { name, depth, usage: u, seconds: times[name], retried: !!u && u.calls > 1 };
  };
  const hasAgent = order.includes("sql_agent");
  const top = order.filter((n) => !(hasAgent && SQL_AGENT_NODES.has(n)));
  const rows = [];
  for (const name of top) {
    rows.push(row(name, 0));
    if (name === "sql_agent") {
      for (const child of order.filter((n) => SQL_AGENT_NODES.has(n))) rows.push(row(child, 1));
    }
  }
  return rows;
}

const CHECK_NAMES = {
  plan_present: "Plan present",
  structure_and_schema: "Tables and columns exist",
  policy: "Role may read these tables",
};

export function humanCheck(name) {
  return CHECK_NAMES[name] || String(name).replace(/_/g, " ");
}
