# Policies Configuration

The `policies` section of [`nl2sql.yaml`](system.md) decides who may read
what. `nl2sql init` writes it to `policies.yaml`.

On your own machine, one line is enough: every caller may read everything.

```yaml
# policies.yaml
allow_all: true
```

For anything shared, write roles. Each role allows and denies tables with
patterns over `datasource.schema.table`:

```yaml
# policies.yaml
default_role: viewer
admin_roles: [admin]
refusal_names_tables: false
roles:
  admin:
    description: Everything
    allow: ["*"]
  analyst:
    description: Sales, except the audit schema and PII tables
    allow: [sales]
    deny:  [sales.audit.*, "sales.*.*_pii"]
  viewer:
    description: Reporting views and support tickets
    allow: [sales.reporting.*, support.public.tickets]
```

The same in Python:

```python
from nl2sql import Policies, Role

Policies(default_role="viewer", admin_roles=["admin"],
         roles={"analyst": Role(description="Sales", allow=["sales"], deny=["sales.audit.*"])})
```

## Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `allow_all` | `false` | Every caller may read everything. It cannot be combined with `default_role` or `roles`. `Policies.allow_all()` is the Python shorthand. |
| `default_role` | none | The role used when the caller gives none. It must name one of `roles`. Without it, a question with no role is refused. |
| `admin_roles` | `[admin]` | The roles `nl2sql-api` lets through on the routes that change datasources, LLMs or the index. See [REST API](api.md). |
| `refusal_names_tables` | `false` | Name the role and the forbidden table in the refusal the user sees. Off, the message is "You do not have permission to see the data this question requires."; the table, the role and the rule that denied it are always in the log and the run trace. The demo (`nl2sql init --demo`) sets it, since showing a refusal is its point. See [Security Model](../security/model.md#hidden-tables-and-strict-refusal). |
| `roles` | `{}` | Role name to its `description`, `allow` patterns and `deny` patterns. |

## Patterns

| Pattern | Matches |
| --- | --- |
| `*` | every table in every datasource |
| `datasource` | every table in every schema of one datasource |
| `datasource.schema.table` | the tables whose datasource, schema and table each match |

Each part is an `fnmatch` glob, so `sales.audit.*` is every table in the
`audit` schema of `sales`, `sales.*.*_pii` is every table ending in `_pii` in
any schema, and `sal*` is every datasource whose id starts with `sal`.

| Rule | Detail |
| --- | --- |
| **Two parts** | Rejected when the config loads, with the fix: "`'sales.orders' has two parts: write 'sales.<schema>.orders', or 'sales.<schema>.*'`". A two-part pattern could mean a table in any schema. |
| **Case** | Matching ignores case, so a deny cannot be dodged by `Orders` against `orders`. |
| **Schemas** | SQLite and DuckDB tables are in `main`. A plan table with no schema is in the adapter's default schema: the one an unqualified name resolves to (`public` on PostgreSQL by default, `dbo` on SQL Server, the connected database on MySQL, `main` on SQLite and DuckDB; `main` when the adapter cannot say). |
| **Within a role** | Deny beats allow. |
| **Across roles** | A caller may read a table if *some* role allows it and that same role does not deny it. One role's deny never takes away what another role allows. |
| **Datasource access** | Implied: a role reaches a datasource when one of its allow patterns can match inside it, unless it denies the whole datasource. |
| **Unknown datasources** | A pattern naming a datasource that is not configured is reported by `nl2sql policy validate`, not refused at startup, because datasources can be added at runtime. |

Access is decided on the schema as well as the table. The plan names the
schema a table is read from, and the SQL is written with it, so an allow of
`warehouse.sales.orders` does not let a plan read `warehouse.audit.orders`.

## Denied tables are hidden

A table the caller may not read never reaches a model. The schema retriever
searches only the readable tables, so the planner's prompt is filled with
tables the caller can use, and a denied table's name, columns, keys,
relationships and data are not in it. The answerability judge and the
decomposer see only the readable table names too.

So a question that needs only denied tables gets "no data for that question"
rather than a refusal. A plan that names a hidden table anyway is still
refused by the logical validator with `SECURITY_VIOLATION`; with
`refusal_names_tables: true` the refusal names the table, for debugging.

## One `Scope` everywhere

Every place that decides access asks the same `Scope`: what one caller may
read in one datasource, built by `PolicyEngine.scope(user, datasource_id)`
from the roles, `allow_all` and `default_role`. The datasource resolver, the
curated matcher, the schema retriever, the logical validator, the executor's
value check and `engine.policies.allows()` all ask it, so they cannot
disagree. A run builds each datasource's scope once and keeps it, so every
step of the run decides the same way.

```python
scope = engine.policies.scope(UserContext(roles=["analyst"]), "sales")
scope.reaches_datasource()          # True
scope.allows("audit", "log")        # False
scope.rule_for("audit", "log")      # "role 'analyst' denies 'sales.audit.*'"
```

## Checking the policies

`nl2sql policy validate` checks that every pattern a role names matches a
configured datasource, against the config `--config` names:

```bash
nl2sql --config nl2sql.yaml policy validate
```

A pattern that matches no configured datasource is `MISSING`, and the command
exits 1. From Python, `engine.policies.validate()` returns the same report;
after `engine.check()`, a pattern that matches only a datasource that failed
to connect is `UNAVAILABLE`.
