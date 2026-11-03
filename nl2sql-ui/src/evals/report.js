// Reads an eval report (`EvalReport`, a finished job's `result`) into what the Evals page shows. The board per
// configuration is `score_config` in nl2sql/evaluation/report.py.

export const percent = (share) => (share === null || share === undefined ? "n/a" : `${Math.round(share * 100)}%`);

// Small sums keep their fractions of a cent; the rest read as dollars and cents.
export function money(amount) {
  if (amount === null || amount === undefined) return "n/a";
  if (amount >= 1 || Number.isInteger(amount * 100)) return `$${amount.toFixed(2)}`;
  return `$${String(Number(amount.toFixed(4)))}`;
}

export function stoppedText(reason, cap) {
  if (!reason) return null;
  if (reason === "max_cost") return `Stopped at the cost cap of ${money(cap)}.`;
  if (reason === "cancelled") return "Cancelled before every case ran.";
  return `Stopped: ${reason}.`;
}

// One line per scorer that failed, "scorer: why". A case with no scorer detail falls back to the record's reason.
export function failureMessages(record) {
  const lines = [];
  for (const [name, score] of Object.entries(record.scores || {})) {
    if (score.passed === true) continue;
    const detail = score.detail || {};
    const why = detail.reason || detail.error;
    if (why) lines.push(`${name}: ${why}`);
  }
  return lines.length ? lines : [record.reason || "The case failed."];
}

function boardView(name, board) {
  const accuracy = board.accuracy || {};
  const roles = (board.summary && board.summary.by_role) || {};
  const latency = (board.latency && board.latency.question) || {};
  return {
    name,
    accuracy: accuracy.overall ?? null,
    interval: accuracy.interval || null,
    generated: accuracy.generated ?? null,
    bySource: Object.entries(accuracy.by_source || {}).map(([source, s]) => ({ name: source, ...s })),
    byRole: Object.entries(roles).map(([role, c]) => {
      const n = { pass: c.pass || 0, fail: c.fail || 0, error: c.error || 0, skip: c.skip || 0 };
      return { role, ...n, total: n.pass + n.fail + n.error + n.skip };
    }),
    cost: board.cost ? board.cost.total : null,
    perQuestion: board.cost ? board.cost.per_question : null,
    p50: latency.p50 ?? null,
    p95: latency.p95 ?? null,
    retries: board.retries ? board.retries.total : 0,
    failing: (board.results || []).filter((r) => r.status === "fail" || r.status === "error").map((r) => ({
      suite: r.suite, id: r.id, role: r.role, status: r.status, question: r.question, sql: r.sql || null,
      messages: failureMessages(r),
    })),
  };
}

export function reportView(report) {
  const r = report || {};
  return {
    mode: r.mode,
    suites: (r.suites || []).map((s) => s.name),
    spent: r.spent ?? null,
    cap: r.max_cost_usd ?? null,
    skipped: r.skipped || 0,
    unknownCost: r.unknown_cost_calls || 0,
    stopped: stoppedText(r.stopped, r.max_cost_usd),
    boards: Object.entries(r.configs || {}).map(([name, board]) => boardView(name, board)),
  };
}
