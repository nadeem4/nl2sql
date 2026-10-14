# Policies Configuration

The `policies` section of [`nl2sql.yaml`](system.md) decides who may read
what. `nl2sql init` writes it to `policies.yaml`.

On your own machine, one line is enough: every caller may read everything.

```yaml
# policies.yaml
allow_all: true
```

For anything shared, write roles:

```yaml
# policies.yaml
default_role: viewer
admin_roles: [admin]
refusal_names_tables: false
roles:
  admin:
    description: Every table in every database
    allowed_datasources: ["*"]
    allowed_tables: ["*"]
  analyst:
    description: Sales, except the audit schema
    allowed_datasources: [sales]
    allowed_tables: ["sales.public.*"]
```

## Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `allow_all` | `false` | Every caller may read everything. It cannot be combined with `default_role` or `roles`. `Policies.allow_all()` is the Python shorthand. |
| `default_role` | none | The role used when the caller gives none. It must name one of `roles`. Without it, a question with no role is refused. |
| `admin_roles` | `[admin]` | The roles `nl2sql-api` lets through on the routes that change datasources, LLMs or the index. See [REST API](api.md). |
| `refusal_names_tables` | `false` | Name the role and the forbidden table in the refusal the user sees. Off, the message is "You do not have permission to see the data this question requires."; the table and role are always in the log and the run trace. The demo (`nl2sql init --demo`) sets it, since showing a refusal is its point. See [Security Model](../security/model.md#strict-refusal). |
| `roles` | `{}` | Role name to policy: `description`, `allowed_datasources` (ids or `*`) and `allowed_tables` (entries below). |

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

```yaml
allowed_tables: [warehouse.sales.orders, "warehouse.reference.*"]
```

### Two-part entries and the default schema

A two-part `datasource.table` entry predates schemas in policies. It is
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
config is loaded.

## One rule everywhere

`table_allowed()` in `nl2sql/auth/rbac.py` is the only place an entry is
matched. The logical validator (which refuses the plan), the schema retriever
(which strips the statistics of tables the role cannot read), the executor's
value check (which only probes readable tables) and the curated matcher (which
only answers with a verified query whose tables are all readable) call it with
the same datasource, schema and table, so they cannot disagree.

## Checking the policies

`nl2sql policy validate` checks that every datasource and table rule a role
names is a configured datasource, against the config `--config` names:

```bash
nl2sql --config nl2sql.yaml policy validate
```

A role naming a datasource that is not configured is `MISSING`, and the
command exits 1. From Python, `engine.policy.validate_policies()` returns the
same report; after `engine.check()`, a role naming a datasource that failed to
connect is `UNAVAILABLE`.
