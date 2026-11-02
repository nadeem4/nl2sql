// What a tile shows, from its last stored run (`TileRun`). Pure: Tile.jsx only draws what this returns.

export const runsById = (runs) => new Map(runs.map((r) => [r.tile_id, r]));

export function ageLabel(at, now = new Date()) {
  const ms = now - new Date(at);
  if (!at || Number.isNaN(ms)) return "";
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 1440) return `${Math.floor(min / 60)} h ago`;
  const days = Math.floor(min / 1440);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

export const refreshedLabel = (at, now) => (at ? `Refreshed ${ageLabel(at, now)}` : "Not refreshed yet");

export function tileState(run) {
  if (!run) return { kind: "pending", message: "Not run yet." };
  const said = (run.error && run.error.message) || "";
  switch (run.status) {
    case "ok":
      return run.chart ? { kind: "chart", spec: run.chart } : { kind: "empty", message: "No rows." };
    case "empty":
      return { kind: "empty", message: "No rows." };
    case "needs_fixing":
      return { kind: "fix", message: said || "This chart can no longer run.", removable: true };
    case "denied":
      return { kind: "denied", message: said || "You may not read this chart's data." };
    case "needs_approval": {
      const needed = run.approvals_needed || [];
      return {
        kind: "approval",
        message: needed.map((a) => a.reason).filter(Boolean).join(" ") || "A person must approve this first.",
        approvalIds: needed.map((a) => a.id),
      };
    }
    case "stopped":
      return { kind: "stopped", message: said || "This refresh was stopped." };
    default:
      return { kind: "error", message: said || "This chart failed." };
  }
}
