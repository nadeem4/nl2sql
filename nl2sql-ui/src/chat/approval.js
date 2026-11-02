// Where a turn that needs approvals stands, given the caller's approval records: waiting, ready (retry with these
// request ids) or denied.

export function approvalState(needed, records) {
  const byId = new Map((records || []).map((r) => [r.id, r]));
  const status = (n) => (byId.get(n.id) || {}).status || "pending";
  if (needed.some((n) => status(n) === "denied")) return { state: "denied", ids: [] };
  if (needed.length && needed.every((n) => status(n) === "granted")) return { state: "ready", ids: needed.map((n) => n.id) };
  return { state: "waiting", ids: [] };
}
