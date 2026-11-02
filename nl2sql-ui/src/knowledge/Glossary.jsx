import React from "react";
import { originOf } from "./origin.js";
import { joinEnd } from "./proposals.js";

// What the engine knows about one datasource (`DatasourceKnowledgeView`): terms, rules, joins, then the tables and
// columns. `origins` says where each piece came from: a knowledge file, the database, or derived.
const ORIGIN = { knowledge: "file", database: "database", derived: "derived" };

function Origin({ origins, kind, name }) {
  const origin = originOf(origins, kind, name);
  return origin ? <span className="art-meta kn-origin">{ORIGIN[origin] || origin}</span> : null;
}

function Section({ title, count, children }) {
  if (!count) return null;
  return (
    <details className="kn-section" open={title === "Terms"}>
      <summary>{title} <span className="art-meta">{count}</span></summary>
      {children}
    </details>
  );
}

const problemText = (p) => (typeof p === "string" ? p : p.message || JSON.stringify(p));

export default function Glossary({ view }) {
  const k = view.knowledge;
  const tables = Object.entries(k.tables || {});
  const columns = Object.entries(k.columns || {});
  return (
    <div className="kn-view">
      {view.problems.length > 0 && (
        <ul className="settings-warn kn-problems">{view.problems.map((p, i) => <li key={i}>{problemText(p)}</li>)}</ul>
      )}
      <Section title="Terms" count={k.terms.length}>
        <dl className="kn-terms">
          {k.terms.map((t) => (
            <div key={t.term}>
              <dt>{t.term}{t.synonyms.length > 0 && <span className="art-meta"> also {t.synonyms.join(", ")}</span>}</dt>
              <dd>{t.definition}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <Section title="Rules" count={k.rules.length}>
        <ul className="kn-list">{k.rules.map((r) => <li key={r.id}>{r.text}</li>)}</ul>
      </Section>
      <Section title="Joins" count={k.joins.length}>
        <ul className="kn-list">
          {k.joins.map((j, i) => (
            <li key={i}>
              <span className="mono">{joinEnd(j.from)} to {joinEnd(j.to)}</span>
              {j.cardinality && j.cardinality !== "unknown" ? ` (${j.cardinality})` : ""}
              {j.notes ? `: ${j.notes}` : ""}
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Tables" count={tables.length}>
        <ul className="kn-list">
          {tables.map(([name, t]) => (
            <li key={name}>
              <span className="mono">{name}</span> <Origin origins={view.origins} kind="tables" name={name} />
              {t.description ? `: ${t.description}` : ""}
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Columns" count={columns.length}>
        <ul className="kn-list">
          {columns.map(([name, c]) => (
            <li key={name}>
              <span className="mono">{name}</span> <Origin origins={view.origins} kind="columns" name={name} />
              {c.description ? `: ${c.description}` : ""}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
