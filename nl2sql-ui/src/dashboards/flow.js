import { refreshBody } from "./filters.js";

// The two multi-call flows of the dashboards page, kept out of the component so they can be tested with a fake
// fetch. `api` is src/api/dashboards.js; `live()` says whether the page is still showing this dashboard.

// Open a dashboard: its stored results first (a read), then a refresh of each due tile on its own. A reply that
// arrives after the page has moved on is dropped, and one failing tile does not stop the rest.
export async function openDashboardFlow(api, id, { onView, onDue, onRun, onTileError, live }) {
  const view = await api.openDashboard(id);
  if (!live()) return;
  onView(view);
  for (const tileId of view.due) {
    if (!live()) return;
    onDue(tileId, true);
    try {
      const run = await api.refreshDashboard(id, refreshBody({ tileId }));
      if (live()) onRun(run, tileId);
    } catch (e) {
      if (live()) onTileError(tileId, e);
    } finally {
      onDue(tileId, false);
    }
  }
}

// "Run again" on a tile that needs approval. The engine runs a gated tile only with the granted approvals passed in,
// so without the request ids (a stored run never has them) one refresh first names them, then the next carries them.
// An approval still waiting answers ApprovalNotFound: `waiting` is true and the run that named the ids is kept.
export async function runAgain(api, id, { tileId, approvalIds = [], filters = [], selection = {} }) {
  const send = (approvals) => api.refreshDashboard(id, refreshBody({ tileId, filters, selection, approvals }));
  let ids = approvalIds;
  let first = null;
  if (ids.length === 0) {
    first = await send([]);
    const tile = first.tiles.find((t) => t.tile_id === tileId);
    ids = tile && tile.status === "needs_approval" ? (tile.approvals_needed || []).map((a) => a.id) : [];
    if (ids.length === 0) return { run: first, waiting: false };
  }
  try {
    return { run: await send(ids), waiting: false };
  } catch (e) {
    if (e && e.code === "ApprovalNotFound") return { run: first, waiting: true };
    throw e;
  }
}
