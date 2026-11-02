import React, { useState } from "react";
import { createWorkspace, deleteWorkspace, renameWorkspace } from "../api/dashboards.js";
import { hashFor } from "../router.js";

// The left column: workspaces, the saved dashboards of the chosen one, and the drafts waiting to be pinned. A
// workspace is deleted only when it is empty; the server says so otherwise and the page shows its sentence.
export default function Workspaces({ workspaces, selected, dashboards, drafts, currentId, onSelect, onChanged, onError }) {
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(null);
  const [text, setText] = useState("");
  const current = workspaces.find((w) => w.id === selected);

  const act = async (fn) => {
    try {
      await fn();
      await onChanged();
    } catch (e) {
      onError(e);
    }
  };
  const create = (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    act(async () => { const w = await createWorkspace(name.trim()); setName(""); onSelect(w.id); });
  };
  const rename = (e) => {
    e.preventDefault();
    if (text.trim()) act(() => renameWorkspace(renaming, text.trim()));
    setRenaming(null);
  };

  return (
    <nav className="dash-side" aria-label="Workspaces">
      <h2 className="dash-h">Workspaces</h2>
      <ul className="dash-list">
        {workspaces.map((w) => (
          <li key={w.id}>
            {renaming === w.id ? (
              <form onSubmit={rename} className="field">
                <input className="dash-input" aria-label="Workspace name" autoFocus value={text} onChange={(e) => setText(e.target.value)} />
                <button type="submit" className="inspect-close">Save</button>
              </form>
            ) : (
              <button type="button" className="dash-item" aria-current={w.id === selected || undefined} onClick={() => onSelect(w.id)}>{w.name}</button>
            )}
          </li>
        ))}
      </ul>
      {current && renaming !== current.id && (
        <div className="dash-row">
          <button type="button" className="inspect-close" onClick={() => { setRenaming(current.id); setText(current.name); }}>Rename</button>
          <button type="button" className="inspect-close dash-danger" disabled={dashboards.length > 0}
            title={dashboards.length ? "Move or delete its dashboards first" : undefined}
            onClick={() => act(() => deleteWorkspace(current.id))}>Delete</button>
        </div>
      )}
      <form onSubmit={create} className="field dash-new">
        <input className="dash-input" aria-label="New workspace" placeholder="New workspace" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit" className="inspect-close" disabled={!name.trim()}>Add</button>
      </form>

      <h2 className="dash-h">{current ? current.name : "Dashboards"}</h2>
      {dashboards.length === 0 && <p className="page-note">No dashboards here yet.</p>}
      <ul className="dash-list">
        {dashboards.map((d) => (
          <li key={d.id}>
            <a className="dash-item" href={hashFor("dashboards", d.id)} aria-current={d.id === currentId || undefined}>
              {d.title}<span className="dash-meta">{d.tiles} {d.tiles === 1 ? "chart" : "charts"}</span>
            </a>
          </li>
        ))}
      </ul>

      {drafts.length > 0 && <h2 className="dash-h">Drafts</h2>}
      <ul className="dash-list">
        {drafts.map((d) => (
          <li key={d.id}>
            <a className="dash-item" href={hashFor("dashboards", d.id)} aria-current={d.id === currentId || undefined}>
              {d.title}<span className="dash-meta">draft</span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
