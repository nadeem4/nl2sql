// Renderers for the planner AST. The engine never lets the model write SQL:
// it emits this plan, the validator gates it, and only then is SQL generated.
// So the plan pane is the honest view of what the model actually produced.

export function exprToText(expr) {
  if (!expr) return "";
  switch (expr.kind) {
    case "column":
      return expr.alias ? `${expr.alias}.${expr.column_name}` : expr.column_name;
    case "literal":
      if (expr.is_null) return "NULL";
      return typeof expr.value === "string" ? `'${expr.value}'` : String(expr.value);
    case "func":
      return `${expr.func_name}(${(expr.args || []).map(exprToText).join(", ")})`;
    case "binary":
      return `${exprToText(expr.left)} ${expr.op} ${exprToText(expr.right)}`;
    case "unary":
      return `${expr.op} ${exprToText(expr.expr)}`;
    case "case": {
      const whens = (expr.whens || [])
        .map((w) => `WHEN ${exprToText(w.condition)} THEN ${exprToText(w.result)}`)
        .join(" ");
      const otherwise = expr.else_expr ? ` ELSE ${exprToText(expr.else_expr)}` : "";
      return `CASE ${whens}${otherwise} END`;
    }
    default:
      return "";
  }
}

// One flat list of labelled lines, so the pane stays a dumb renderer.
export function planSections(plan) {
  if (!plan) return [];
  const sections = [];
  const push = (label, items) => {
    if (items && items.length) sections.push({ label, items });
  };

  push(
    "Tables",
    (plan.tables || []).map((t) => `${t.schema_name ? `${t.schema_name}.` : ""}${t.name} AS ${t.alias}`)
  );
  push(
    "Joins",
    (plan.joins || []).map(
      (j) => `${j.join_type.toUpperCase()} ${j.left_alias} = ${j.right_alias} on ${exprToText(j.condition)}`
    )
  );
  push(
    "Select",
    (plan.select_items || []).map((s) => (s.alias ? `${exprToText(s.expr)} AS ${s.alias}` : exprToText(s.expr)))
  );
  if (plan.where) push("Where", [exprToText(plan.where)]);
  push("Group by", (plan.group_by || []).map((g) => exprToText(g.expr)));
  if (plan.having) push("Having", [exprToText(plan.having)]);
  push("Order by", (plan.order_by || []).map((o) => `${exprToText(o.expr)} ${o.direction.toUpperCase()}`));

  const limits = [];
  if (plan.limit != null) limits.push(`LIMIT ${plan.limit}`);
  if (plan.offset != null) limits.push(`OFFSET ${plan.offset}`);
  if (plan.distinct) limits.unshift("DISTINCT");
  push("Limits", limits);

  return sections;
}
