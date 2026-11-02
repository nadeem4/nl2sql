// "Why this answer": the engine's typed explanation as labelled lines. The planner's note is the model's own words,
// so it is flagged and the page labels it as such; everything else is derived from the plan.

const LISTS = [
  ["tables", "Tables"], ["joins", "Joins"], ["filters", "Filters"], ["groupings", "Grouped by"],
  ["measures", "Measures"], ["order", "Order"],
];

export function whySections(result) {
  const steps = (result && result.explanation && result.explanation.steps) || [];
  return steps.map((step) => {
    const parts = [];
    for (const [key, label] of LISTS) if ((step[key] || []).length) parts.push({ label, items: step[key], model: false });
    if ((step.terms || []).length) {
      parts.push({ label: "Terms", model: false, items: step.terms.map((t) => `${t.term}: ${t.definition}${t.source ? ` (${t.source})` : ""}`) });
    }
    if ((step.rules || []).length) parts.push({ label: "Rules", items: step.rules, model: false });
    if ((step.assumptions || []).length) parts.push({ label: "Assumptions", items: step.assumptions, model: false });
    if (step.planner_note) parts.push({ label: "Planner note", items: [step.planner_note], model: true });
    return { source: step.source, parts };
  });
}
