import React, { useState } from "react";
import { filterValues } from "../api/dashboards.js";
import { activeCount, inputType, setValues } from "./filters.js";

// A dashboard's filters. Applying them refreshes every chart with the values and shows the result; nothing is stored,
// and Clear brings back the stored results. An `in` filter offers the column's values (read when it is first opened);
// a `between` filter takes two ends.
function InFilter({ dashboardId, filter, values, onChange, onError }) {
  const [options, setOptions] = useState(null);
  const open = (e) => {
    if (!e.currentTarget.open || options) return;
    setOptions([]);
    filterValues(dashboardId, filter.id).then(setOptions).catch((err) => { setOptions(null); onError(err); });
  };
  const toggle = (v) => onChange(values.includes(v) ? values.filter((x) => x !== v) : [...values, v]);
  return (
    <details className="dash-filter" onToggle={open}>
      <summary>{filter.label}{values.length > 0 && <span className="dash-count">{values.length}</span>}</summary>
      <div className="dash-options">
        {options === null || options.length === 0 ? <span className="page-note">Loading values</span> : options.map((v) => (
          <label key={String(v)} className="check">
            <input type="checkbox" checked={values.includes(v)} onChange={() => toggle(v)} />
            {String(v)}
          </label>
        ))}
      </div>
    </details>
  );
}

function BetweenFilter({ filter, values, onChange }) {
  const [from = "", to = ""] = values;
  const type = inputType(filter);
  return (
    <div className="dash-filter dash-between" role="group" aria-label={filter.label}>
      <span>{filter.label}</span>
      <input className="dash-input" type={type} aria-label={`${filter.label} from`} value={from} onChange={(e) => onChange([e.target.value, to])} />
      <span aria-hidden="true">to</span>
      <input className="dash-input" type={type} aria-label={`${filter.label} to`} value={to} onChange={(e) => onChange([from, e.target.value])} />
    </div>
  );
}

export default function Filters({ dashboard, selection, filtered, busy, onSelect, onApply, onClear, onError, editing, onRemove }) {
  const filters = dashboard.filters;
  if (!filters.length) return null;
  const ready = activeCount(filters, selection);
  return (
    <div className="dash-filters" role="group" aria-label="Filters">
      {filters.map((f) => {
        const values = selection[f.id] || [];
        const change = (v) => onSelect(setValues(selection, f.id, v));
        return (
          <div key={f.id} className="dash-filter-wrap">
            {f.op === "between"
              ? <BetweenFilter filter={f} values={values} onChange={change} />
              : <InFilter dashboardId={dashboard.id} filter={f} values={values} onChange={change} onError={onError} />}
            {editing && <button type="button" className="inspect-close dash-danger" disabled={busy} onClick={() => onRemove(f.id)}>Remove filter</button>}
          </div>
        );
      })}
      <button type="button" className="index-rebuild is-primary" disabled={busy || ready === 0} onClick={onApply}>Apply</button>
      {filtered && <button type="button" className="index-rebuild" disabled={busy} onClick={onClear}>Clear</button>}
    </div>
  );
}
