# Diagrams

!!! note "Arrives with K2 (Wave C, 0.9)"
    This page is a stub. `engine.datasources.diagram` exists with its final signature and raises
    `NotImplementedError` naming K2. The design is
    [Wave C, section 4](../architecture/wave_c.md#diagrams).

Two Mermaid views, written to read the same on a light or a dark page (no `%%{init}` line, no `style`,
`classDef` or `linkStyle`):

- **The answer view**, `nl2sql.diagram_for(result)`: the tables each sub-query read, one edge per join.
- **The schema view**, `engine.datasources.diagram(datasource_id, *, user_context, tables=None)`: an
  `erDiagram` of the tables the caller's scope reaches. The caller is required.