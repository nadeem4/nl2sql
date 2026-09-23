import React, { useState } from "react";

// The database, read from the engine's own indexed snapshot: the same schema
// the planner is given. Visible before any question is asked. Tables the
// current plan reads, and tables the role was refused, are marked.
//
// `datasources` is every registered database. With more than one the heading
// carries a switcher, so each of them can be looked at rather than only the
// one the demo opens on; with one there is nothing to choose and the panel is
// exactly what it always was.
export default function SchemaPanel({ schema, used = [], denied = [], role,
                                     datasources = [], onDatasource }) {
  const [open, setOpen] = useState(() => new Set());
  const several = datasources.length > 1;

  const toggle = (name) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  if (!schema) {
    return (
      <section className="schema" aria-labelledby="schema-heading">
        <h2 id="schema-heading">Database</h2>
        <p className="quiet">Reading the schema.</p>
      </section>
    );
  }

  if (!schema.tables.length) {
    return (
      <section className="schema" aria-labelledby="schema-heading">
        <h2 id="schema-heading">Database</h2>
        <p className="quiet">
          No indexed schema for <code>{schema.datasource_id}</code>. Run <code>nl2sql index</code> in the demo
          directory.
        </p>
      </section>
    );
  }

  const rows = schema.tables.reduce((n, t) => n + (t.row_count || 0), 0);

  return (
    <section className="schema" aria-labelledby="schema-heading">
      <h2 id="schema-heading">
        Database {!several && <code className="ds">{schema.datasource_id}</code>}
      </h2>
      {several && (
        <p className="schema-pick">
          <label htmlFor="schema-datasource">Showing</label>
          <select id="schema-datasource" value={schema.datasource_id}
                  onChange={(e) => onDatasource && onDatasource(e.target.value)}>
            {datasources.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </p>
      )}
      <p className="schema-sum">
        {schema.tables.length} tables, {rows.toLocaleString()} rows. Open a table for its columns and keys.
      </p>
      {several && (
        <p className="schema-cross" id="schema-cross">
          Each question is answered from one database; joining across them is planned.
        </p>
      )}
      <ul className="tables">
        {schema.tables.map((table) => {
          const isOpen = open.has(table.name);
          const refs = [...new Set(table.foreign_keys.map((fk) => fk.references_table))];
          const flag = denied.includes(table.name) ? "denied" : used.includes(table.name) ? "used" : null;
          return (
            <li key={table.name} data-flag={flag || undefined}>
              <button
                className="table-head"
                onClick={() => toggle(table.name)}
                aria-expanded={isOpen}
                aria-controls={`cols-${table.name}`}
              >
                <span className="caret" aria-hidden="true" />
                <span className="table-name">{table.name}</span>
                {flag === "used" && <span className="flag">in plan</span>}
                {flag === "denied" && <span className="flag">refused{role ? ` for ${role}` : ""}</span>}
                {table.row_count != null && <span className="table-rows">{table.row_count.toLocaleString()}</span>}
                {refs.length > 0 && <span className="refs">refers to {refs.join(", ")}</span>}
              </button>
              {isOpen && (
                <div className="table-body" id={`cols-${table.name}`}>
                  {table.description && <p className="quiet">{table.description}</p>}
                  <table className="cols">
                    <caption className="visually-hidden">Columns of {table.name}</caption>
                    <tbody>
                      {table.columns.map((column) => (
                        <tr key={column.name}>
                          <th scope="row" className="col-name">
                            {column.name}
                            {column.primary_key && <span className="pk" title="primary key">key</span>}
                          </th>
                          <td className="col-type">{column.type}</td>
                          <td className="col-null">{column.nullable ? "" : "not null"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {table.foreign_keys.length > 0 && (
                    <ul className="fks" aria-label="Foreign keys">
                      {table.foreign_keys.map((fk, i) => (
                        <li key={i}>
                          <code>{fk.columns.join(", ")}</code> references{" "}
                          <code>{fk.references_table}.{fk.references_columns.join(", ")}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
