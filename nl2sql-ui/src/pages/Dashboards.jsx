import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  arrange, deleteDashboard, listDashboards, listWorkspaces, moveDashboard, openDashboard, pinDashboard,
  refreshDashboard, removeTile, renameDashboard, renameTile, setFilters, setSchedule, setTileChart,
} from "../api/dashboards.js";
import { hashFor } from "../router.js";
import Grid from "../dashboards/Grid.jsx";
import Filters from "../dashboards/Filters.jsx";
import Schedule from "../dashboards/Schedule.jsx";
import Workspaces from "../dashboards/Workspaces.jsx";
import { activeCount, refreshBody, setValues } from "../dashboards/filters.js";
import { openDashboardFlow, runAgain } from "../dashboards/flow.js";
import { arrangeBody, order, resize, shift } from "../dashboards/layout.js";
import { oldestAt, refreshedLabel, tileState } from "../dashboards/tile.js";

// Dashboards and workspaces. A dashboard is built in chat and pinned whole; this page opens it with its stored
// results (opening never runs a query), refreshes the tiles that are due, and lets a person rename, arrange, filter,
// schedule, move or delete it. None of that calls a model. The multi-call flows are in dashboards/flow.js.
function Notice({ error }) {
  if (!error) return null;
  return (
    <p className="rows-error" role="alert">{error.message || String(error)}{error.fix ? ` ${error.fix}` : ""}</p>
  );
}

const api = { openDashboard, refreshDashboard };

export default function Dashboards({ param }) {
  const [workspaces, setWorkspaces] = useState(null);
  const [selected, setSelected] = useState(null);
  const [list, setList] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [runs, setRuns] = useState(new Map());
  const [refreshing, setRefreshing] = useState(new Set());
  const [selection, setSelection] = useState({});
  const [applied, setApplied] = useState({});
  const [editing, setEditing] = useState(false);
  const [panel, setPanel] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [target, setTarget] = useState("");
  const live = useRef(0);
  const wanted = useRef(null);

  const fail = useCallback((e) => setError(e), []);

  // `workspaceId` is the one to show; without it, the workspace of the dashboard being opened, else the first.
  const loadSide = useCallback(async (workspaceId) => {
    const all = await listWorkspaces();
    setWorkspaces(all);
    const want = workspaceId || wanted.current;
    const chosen = all.find((w) => w.id === want) ? want : all[0] && all[0].id;
    setSelected(chosen || null);
    setList(chosen ? await listDashboards({ workspaceId: chosen }) : []);
    setDrafts(await listDashboards({ status: "draft" }));
  }, []);

  useEffect(() => { loadSide(null).catch(fail); }, [loadSide, fail]);

  const mark = (ids, on) => setRefreshing((prev) => {
    const next = new Set(prev);
    ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
    return next;
  });

  const takeRuns = (tiles, replace) => {
    setNow(new Date());
    setRuns((prev) => {
      const next = replace ? new Map() : new Map(prev);
      tiles.forEach((r) => next.set(r.tile_id, r));
      return next;
    });
  };

  // Show the dashboard with its stored results, then refresh the tiles that are due. Used on opening and wherever the
  // stored results were dropped (a filter removed) or are wanted back (Clear). Busy while the due tiles run, so no
  // edit or refresh can collide with them.
  const load = async (id, token) => {
    setBusy(true);
    try {
      await openDashboardFlow(api, id, {
        live: () => live.current === token,
        onView: (view) => {
          setDashboard(view.dashboard);
          takeRuns(view.runs, true);
          wanted.current = view.dashboard.workspace_id;
          loadSide(view.dashboard.workspace_id).catch(fail);
        },
        onDue: (tileId, on) => mark([tileId], on),
        onRun: (run) => takeRuns(run.tiles, false),
        onTileError: (tileId, e) => takeRuns([{ tile_id: tileId, status: "error", chart: null, error: { message: e.message } }], false),
      });
    } catch (e) {
      if (live.current === token) setError(e);
    } finally {
      if (live.current === token) setBusy(false);
    }
  };

  // Open the dashboard in the address bar.
  useEffect(() => {
    const token = ++live.current;
    setDashboard(null);
    setRuns(new Map());
    setRefreshing(new Set());
    setSelection({});
    setApplied({});
    setEditing(false);
    setPanel(null);
    setError(null);
    setBusy(false);
    if (param) load(param, token);
    return () => { live.current += 1; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [param]);

  // Every action below drops its reply when the page has moved to another dashboard since it began.
  const guard = async (fn) => {
    const token = live.current;
    const fresh = () => live.current === token;
    setBusy(true);
    setError(null);
    try {
      await fn(fresh);
    } catch (e) {
      if (fresh()) setError(e);
    } finally {
      if (fresh()) setBusy(false);
    }
  };

  // Every edit answers the changed Dashboard; the stored results stay as they are.
  const edit = (call) => guard(async (fresh) => {
    const next = await call();
    if (!fresh()) return;
    setDashboard(next);
    setList(await listDashboards({ workspaceId: next.workspace_id }));
  });

  // Refresh one tile (`tileId`) or all, and show the result in place.
  const run = async (fresh, body, tileId) => {
    const ids = tileId ? [tileId] : dashboard.tiles.map((t) => t.id);
    mark(ids, true);
    try {
      const result = await refreshDashboard(dashboard.id, body);
      if (fresh()) takeRuns(result.tiles, !tileId);
    } finally {
      mark(ids, false);
    }
  };
  const refresh = (body, tileId) => guard((fresh) => run(fresh, body, tileId));

  const again = (tileId, approvalIds) => guard(async (fresh) => {
    mark([tileId], true);
    try {
      const out = await runAgain(api, dashboard.id, {
        tileId, approvalIds, filters: dashboard.filters, selection: applied,
      });
      if (!fresh()) return;
      if (out.run) takeRuns(out.run.tiles, false);
      if (out.waiting) setError({ message: "Still waiting for someone to approve it. Try again once it is granted." });
    } finally {
      mark([tileId], false);
    }
  });

  const arranged = (items) => edit(() => arrange(dashboard.id, arrangeBody(items).arrangements));

  const apply = () => { setApplied(selection); refresh(refreshBody({ filters: dashboard.filters, selection })); };
  // Clear brings back the stored results: open the dashboard again rather than refresh it.
  const clear = () => { setSelection({}); setApplied({}); load(dashboard.id, live.current); };
  // Removing a filter drops every stored result on the server, so open again to get them back.
  const dropFilter = (id) => {
    setSelection(setValues(selection, id, []));
    setApplied(setValues(applied, id, []));
    guard(async (fresh) => {
      const next = await setFilters(dashboard.id, dashboard.filters.filter((f) => f.id !== id));
      if (!fresh()) return;
      setDashboard(next);
      await load(next.id, live.current);
    });
  };

  const actions = {
    // A tile waiting on an approval runs again with the granted ids; any other tile is a plain refresh.
    refreshTile: (id) => {
      const state = tileState(runs.get(id));
      return state.kind === "approval"
        ? again(id, state.approvalIds)
        : refresh(refreshBody({ tileId: id, filters: dashboard.filters, selection: applied }), id);
    },
    remove: (id) => edit(() => removeTile(dashboard.id, id)),
    title: (id, title) => edit(() => renameTile(dashboard.id, id, title)),
    // The stored result keeps the old chart until it runs again, so the changed tile runs once.
    chart: (id, chart) => guard(async (fresh) => {
      const next = await setTileChart(dashboard.id, id, chart);
      if (!fresh()) return;
      setDashboard(next);
      await run(fresh, refreshBody({ tileId: id, filters: next.filters, selection: applied }), id);
    }),
    shift: (id, by) => arranged(shift(order(dashboard.tiles), id, by)),
    size: (id, dw, dh) => arranged(resize(order(dashboard.tiles), id, dw, dh)),
  };

  const rename = () => {
    const title = window.prompt("Dashboard title", dashboard.title);
    if (title && title.trim() && title.trim() !== dashboard.title) edit(() => renameDashboard(dashboard.id, title.trim()));
  };
  const destroy = () => {
    if (!window.confirm(`Delete "${dashboard.title}"? Its charts and results go with it.`)) return;
    guard(async () => {
      await deleteDashboard(dashboard.id);
      await loadSide(dashboard.workspace_id);
      window.location.hash = hashFor("dashboards");
    });
  };
  const place = (call) => guard(async (fresh) => {
    const next = await call();
    if (!fresh()) return;
    setDashboard(next);
    await loadSide(next.workspace_id);
  });

  const filtered = dashboard ? activeCount(dashboard.filters, applied) > 0 : false;
  const refreshedAt = oldestAt([...runs.values()]);
  const others = (workspaces || []).filter((w) => dashboard && w.id !== dashboard.workspace_id);
  const draft = dashboard && dashboard.status === "draft";
  const choices = draft ? workspaces || [] : others;
  const chosen = choices.some((w) => w.id === target) ? target : choices[0] ? choices[0].id : "";

  return (
    <div className="sheet dash">
      <Workspaces workspaces={workspaces || []} selected={selected} dashboards={list} drafts={drafts}
        currentId={param} onSelect={(id) => { setSelected(id); listDashboards({ workspaceId: id }).then(setList).catch(fail); }}
        onChanged={(id) => loadSide(id || selected)} onError={fail} />
      <section className="dash-main" aria-live="polite">
        <Notice error={error} />
        {!param && <p className="page-note">Pick a dashboard, or ask the chat to build one and pin it to a workspace.</p>}
        {param && !dashboard && !error && <p className="page-note">Opening the dashboard</p>}
        {dashboard && (
          <>
            <header className="dash-head">
              <div>
                <h1 className="dash-title">{dashboard.title}</h1>
                <p className="page-note">
                  {draft ? "Draft. Pin it to a workspace to keep it." : refreshedLabel(refreshedAt, now)}
                  {filtered && " Showing a filtered result; nothing is saved."}
                </p>
              </div>
              <div className="dash-actions">
                <button type="button" className="index-rebuild is-primary" disabled={busy} onClick={apply}>Refresh</button>
                <button type="button" className="index-rebuild" aria-pressed={editing} onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</button>
                <button type="button" className="index-rebuild" aria-pressed={panel === "schedule"} onClick={() => setPanel(panel ? null : "schedule")}>Schedule</button>
              </div>
            </header>

            {panel === "schedule" && (
              <Schedule schedule={dashboard.schedule} busy={busy} onSave={(m) => edit(() => setSchedule(dashboard.id, m))} />
            )}

            <Filters dashboard={dashboard} selection={selection} filtered={filtered} busy={busy} editing={editing}
              onSelect={setSelection} onError={fail} onApply={apply} onClear={clear} onRemove={dropFilter} />

            <Grid dashboard={dashboard} runs={runs} refreshing={refreshing} editing={editing} now={now} filtered={filtered} actions={actions} />

            {editing && (
              <div className="dash-manage">
                <button type="button" className="index-rebuild" disabled={busy} onClick={rename}>Rename dashboard</button>
                {(draft || choices.length > 0) && (
                  <div className="field">
                    {choices.length > 0 && (
                      <>
                        <label htmlFor="dash-target">{draft ? "Pin to" : "Move to"}</label>
                        <select id="dash-target" value={chosen} onChange={(e) => setTarget(e.target.value)}>
                          {choices.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                      </>
                    )}
                    <button type="button" className="index-rebuild" disabled={busy}
                      onClick={() => place(() => (draft ? pinDashboard(dashboard.id, chosen ? { workspaceId: chosen } : {}) : moveDashboard(dashboard.id, chosen)))}>
                      {draft ? "Pin" : "Move"}
                    </button>
                  </div>
                )}
                <button type="button" className="inspect-close dash-danger" disabled={busy} onClick={destroy}>Delete dashboard</button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
