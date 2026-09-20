import React, { useState } from "react";

// The database, read from the engine's own indexed snapshot -- the same schema
// the planner was given. Visible before any question is asked.
export default function SchemaPanel({ schema }) {
  const [open, setOpen] = useState(() => new Set());

  if (!schema) return <section className="panel"><h2>Database</h2><p className="muted">Loading the schema...</p></section>;

  const toggle = (name) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  if (!schema.tables.length) {
    return (
      <section className="panel">
        <h2>Database</h2>
        <p className="muted">
          No indexed schema for <code>{schema.datasource_id}</code>. Run <code>nl2sql index</code> in the demo
          directory.
        </p>
      </section>
    );
  }

  return (
    <section className="panel schema">
      <h2>
        Database <span className="muted">{schema.datasource_id}</span>
        <span className="count">{schema.tables.length} tables</span>
      </h2>
      <ul className="tables">
        {schema.tables.map((table) => (
          <li key={table.name}>
            <button className="table-head" onClick={() => toggle(table.name)} aria-expanded={open.has(table.name)}>
              <span className="chevron">{open.has(table.name) ? "▾" : "▸"}</span>
              <span className="table-name">{table.name}</span>
              <span className="muted">{table.columns.length} cols</span>
              {table.row_count != null && <span className="muted">{table.row_count} rows</span>}
            </button>
            {open.has(table.name) && (
              <div className="table-body">
                {table.description && <p className="muted">{table.description}</p>}
                <table>
                  <tbody>
                    {table.columns.map((column) => (
                      <tr key={column.name}>
                        <td className="col-name">
                          {column.name}
                          {column.primary_key && <span className="pk" title="primary key">PK</span>}
                        </td>
                        <td className="muted">{column.type}</td>
                        <td className="muted">{column.nullable ? "" : "not null"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {table.foreign_keys.map((fk, i) => (
                  <p key={i} className="fk">
                    {fk.columns.join(", ")} &rarr; {fk.references_table}.{fk.references_columns.join(", ")}
                  </p>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
