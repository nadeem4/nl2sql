# Policies Configuration

Policies live in `configs/policies.json` and define role-based access rules for
datasources and tables.

## File structure

```json
{
  "version": 1,
  "roles": {
    "admin": {
      "description": "System Administrator",
      "role": "admin",
      "allowed_datasources": ["*"],
      "allowed_tables": ["*"]
    }
  }
}
```

## Fields

- `version`: schema version (currently `1`)
- `roles`: map of role name → policy
  - `description`: human-readable role description
  - `role`: role ID used for logging and auditing
  - `allowed_datasources`: list of datasource IDs or `*`
  - `allowed_tables`: list of table entries (see below)

## Table entries

| Entry | Allows |
| --- | --- |
| `*` | every table in every datasource |
| `datasource.*` | every table in every schema of one datasource |
| `datasource.schema.*` | every table in one schema |
| `datasource.schema.table` | one table in one schema |
| `datasource.table` | that table in the datasource's **default schema only** |

Access is decided on the schema as well as the table. The plan names the
schema a table is read from, and the SQL is written with it, so a grant of
`sales.orders` does not let a plan read `audit.orders`:

```json
"allowed_tables": ["warehouse.sales.orders", "warehouse.reference.*"]
```

### Two-part entries and the default schema

A two-part `datasource.table` entry predates schemas in the policy file. It is
read as that table in the datasource's default schema, never in any schema:

- The default schema is the one the adapter reports: the schema an unqualified
  table name resolves to on that connection (`public` on PostgreSQL by default,
  `dbo` on SQL Server, the connected database on MySQL, `main` on SQLite and
  DuckDB).
- If an adapter cannot report one, `main` is the default.
- A table the plan names without a schema is in the default schema.

So on a PostgreSQL datasource `warehouse`, `warehouse.orders` allows
`public.orders` and refuses `audit.orders`. On SQLite and DuckDB, which have
the one schema, two-part entries mean what they always did.

The first two-part entry loaded logs a warning, once per process, saying it is
read as the default schema. To grant a table outside the default schema, write
the three-part form.

An entry has two or three dot-separated parts (or is `*`), no part is empty,
and `*` may only be the last part: `datasource.*.orders` is rejected when the
file is loaded.

## One rule everywhere

`table_allowed()` in `nl2sql/auth/rbac.py` is the only place an entry is
matched. The logical validator (which refuses the plan), the schema retriever
(which strips the statistics of tables the role cannot read), the executor's
value check (which only probes readable tables) and the curated matcher (which
only answers with a verified query whose tables are all readable) call it with
the same datasource, schema and table, so they cannot disagree.
