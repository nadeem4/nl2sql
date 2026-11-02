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
import { refreshBody } from "../dashboards/filters.js";
import { arrangeBody, order, resize, shift } from "../dashboards/layout.js";
import { refreshedLabel, runsById } from "../dashboards/tile.js";

// Dashboards and workspaces. A dashboard is built in chat and pinned whole; this page opens it with its stored
// results (opening never runs a query), refreshes the tiles that are due, and lets a person rename, arrange, filter,
// schedule, move or delete it. None of that calls a model.
function Notice({ error }) {
  if (!error) return null;
  return (
    <p className="rows-error" role="alert">{error.message || String(error)}{error.fix ? ` ${error.fix}` : ""}</p>
  );
}

export default function Dashboards({ param }) {
  const [workspaces, setWorkspaces] = useState(null);
  const [selected, setSelected] = useState(null);
  const [list, setList] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [runs, setRuns] = useState(new Map());
  const [refreshedAt, setRefreshedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(new Set());
  const [selection, setSelection] = useState({});
  const [filtered, setFiltered] = useState(false);
  const [editing, setEditing] = useState(false);
  const [panel, setPanel] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [target, setTarget] = useState("");
  const live = useRef(0);

  const fail = useCallback((e) => setError(e), []);

  const loadSide = useCallback(async (workspaceId) => {
    const all = await listWorkspaces();
    setWorkspaces(all);
    const chosen = all.find((w) => w.id === workspaceId) ? workspaceId : all[0] && all[0].id;
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

  // Open the dashboard in the address bar: its stored results first, then a refresh of the tiles that are due.
  useEffect(() => {
    const token = ++live.current;
    setDashboard(null);
    setRuns(new Map());
    setSelection({});
    setFiltered(false);
    setEditing(false);
    setPanel(null);
    setError(null);
    if (!param) return undefined;
    (async () => {
      try {
        const view = await openDashboard(param);
        if (live.current !== token) return;
        setDashboard(view.dashboard);
        setRefreshedAt(view.refreshed_at);
        takeRuns(view.runs, true);
        if (view.dashboard.workspace_id !== selected) {
          setSelected(view.dashboard.workspace_id);
          setList(await listDashboards({ workspaceId: view.dashboard.workspace_id }));
        }
        for (const id of view.due) {
          if (live.current !== token) return;
          mark([id], true);
          try {
            const run = await refreshDashboard(param, refreshBody({ tileId: id }));
            if (live.current === token) { takeRuns(run.tiles, false); setRefreshedAt(run.at); }
          } finally {
            mark([id], false);
          }
        }
      } catch (e) {
        if (live.current === token) setError(e);
      }
    })();
    return () => { live.current += 1; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [param]);

  const guard = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  // Every edit answers the changed Dashboard; the stored results stay as they are.
  const edit = (call) => guard(async () => {
    const next = await call();
    setDashboard(next);
    setList(await listDashboards({ workspaceId: next.workspace_id }));
  });

  const refresh = (body, tileId) => guard(async () => {
    const ids = tileId ? [tileId] : dashboard.tiles.map((t) => t.id);
    mark(ids, true);
    try {
      const run = await refreshDashboard(dashboard.id, body);
      takeRuns(run.tiles, !tileId);
      setRefreshedAt(run.at);
      setFiltered(Boolean(body.filters));
    } finally {
      mark(ids, false);
    }
  });

  const arranged = (items) => edit(() => arrange(dashboard.id, arrangeBody(items).arrangements));

  const actions = {
    refreshTile: (id) => refresh(refreshBody({ tileId: id, filters: dashboard.filters, selection }), id),
    remove: (id) => edit(() => removeTile(dashboard.id, id)),
    title: (id, title) => edit(() => renameTile(dashboard.id, id, title)),
    chart: (id, chart) => edit(() => setTileChart(dashboard.id, id, chart)),
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
  const place = (call) => guard(async () => {
    const next = await call();
    setDashboard(next);
    await loadSide(next.workspace_id);
  });

  const others = (workspaces || []).filter((w) => dashboard && w.id !== dashboard.workspace_id);
  const draft = dashboard && dashboard.status === "draft";
  const choices = draft ? workspaces || [] : others;
  const chosen = choices.some((w) => w.id === target) ? target : choices[0] ? choices[0].id : "";

  return (
    <div className="sheet dash">
      <Workspaces workspaces={workspaces || []} selected={selected} dashboards={list} drafts={drafts}
        currentId={param} onSelect={(id) => { setSelected(id); listDashboards({ workspaceId: id }).then(setList).catch(fail); }}
        onChanged={() => loadSide(selected)} onError={fail} />
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
                <button type="button" className="index-rebuild is-primary" disabled={busy} onClick={() => refresh(refreshBody({ filters: dashboard.filters, selection }))}>Refresh</button>
                <button type="button" className="index-rebuild" aria-pressed={editing} onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</button>
                <button type="button" className="index-rebuild" aria-pressed={panel === "schedule"} onClick={() => setPanel(panel ? null : "schedule")}>Schedule</button>
              </div>
            </header>

            {panel === "schedule" && (
              <Schedule schedule={dashboard.schedule} busy={busy} onSave={(m) => edit(() => setSchedule(dashboard.id, m))} />
            )}

            <Filters dashboard={dashboard} selection={selection} filtered={filtered} busy={busy} editing={editing}
              onSelect={setSelection} onError={fail}
              onApply={() => refresh(refreshBody({ filters: dashboard.filters, selection }))}
              onClear={() => { setSelection({}); refresh({}); }}
              onRemove={(id) => edit(() => setFilters(dashboard.id, dashboard.filters.filter((f) => f.id !== id)))} />

            <Grid dashboard={dashboard} runs={runs} refreshing={refreshing} editing={editing} now={now} actions={actions} />

            {editing && (
              <div className="dash-manage">
                <button type="button" className="index-rebuild" disabled={busy} onClick={rename}>Rename dashboard</button>
                {choices.length > 0 && (
                  <div className="field">
                    <label htmlFor="dash-target">{draft ? "Pin to" : "Move to"}</label>
                    <select id="dash-target" value={chosen} onChange={(e) => setTarget(e.target.value)}>
                      {choices.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                    <button type="button" className="index-rebuild" disabled={busy}
                      onClick={() => place(() => (draft ? pinDashboard(dashboard.id, { workspaceId: chosen }) : moveDashboard(dashboard.id, chosen)))}>
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
