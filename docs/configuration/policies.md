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
| `check` | none | A [policy check](#a-policy-check): `{type: <name>, <its keys>}` naming a registered one, or, in Python, a check object. What it grants is intersected with the roles. |

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
from the roles, `allow_all`, `default_role` and the policy check, if there is
one. The datasource resolver, the
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

## A policy check

When access is decided somewhere else (an IAM service, OPA, Cedar, OpenFGA,
Unity Catalog), a policy check asks it. A check is a plugin: it gets the
caller and a datasource id, and returns a `Grant` of allow and deny patterns,
written exactly like a role's.

```yaml
# policies.yaml
roles:
  analyst:
    description: Sales
    allow: [sales]
check:
  type: corp_iam               # a registered policy check
  url: "${env:IAM_URL}"        # the check's own keys
```

| Rule | Detail |
| --- | --- |
| **Intersection** | A table is readable only if the roles allow it *and* the check's grant allows it and does not deny it. Neither can widen the other. With `allow_all: true` and a check, the check alone decides. |
| **Fails closed** | Any exception from the check, or anything it returns that is not a valid `Grant` (a pattern with two parts, say), denies that datasource for the run. The failure is logged as a warning with the check's name. There is no fail-open option. |
| **How often** | Once per caller and datasource per run. Nothing is cached across runs, so a change in the outside service applies to the next question. |
| **Timeouts** | The check owns them, as an HTTP client timeout for example. The pipeline's `timeout_s` bounds the run anyway. |
| **The log** | When the check is what refuses a table, `rule_for` names it: "policy check 'corp_iam' does not allow 'sales.audit.log'". |

No check is built in. A check implements `nl2sql_sdk.policy.PolicyCheck`
(`interface_version = 1`, a `config_model` for its block, `from_config(cfg)`
and `grant(user, datasource_id)`). Register it in code with
`registry.add_policy_check(name, cls)` or `@registry.policy_check(name)`, or
publish it in the `nl2sql.policy_checks` entry point group. An unknown
`type`, or a misspelt key in the block, is a config error that names
`policies.check`. In Python, `Policies(..., check=obj)` uses a check object
as given.

A check that asks an HTTP service:

```python
import httpx
from pydantic import BaseModel, ConfigDict

from nl2sql_sdk.policy import Grant, UserContext


class CorpIamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_s: float = 2.0


class CorpIam:
    interface_version = 1
    config_model = CorpIamConfig

    def __init__(self, cfg: CorpIamConfig):
        self.client = httpx.Client(base_url=cfg.url, timeout=cfg.timeout_s)

    @classmethod
    def from_config(cls, cfg: CorpIamConfig) -> "CorpIam":
        return cls(cfg)

    def grant(self, user: UserContext, datasource_id: str) -> Grant:
        # Any error here (a timeout, a 500) denies the datasource for this run.
        response = self.client.get("/grants", params={"user": user.user_id, "datasource": datasource_id})
        response.raise_for_status()
        return Grant.model_validate(response.json())  # {"allow": [...], "deny": [...]}
```

Publish it in the package that ships it:

```toml
[project.entry-points."nl2sql.policy_checks"]
corp_iam = "corp_nl2sql.iam:CorpIam"
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
