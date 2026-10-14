# Policies API

## Purpose
`engine.policies`: who may read what. The roles, what a caller may read in a
datasource, whether a caller may read a table, and whether the patterns match
the datasources.

## Responsibilities
- List the configured roles.
- Give the `Scope` a caller has in one datasource, and check one table with it.
- Check that every pattern matches a datasource the engine has.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/policy_api.py` (`PoliciesAPI`, internal)
- `packages/sdk/src/nl2sql_sdk/policy.py` (`UserContext`, `Grant`, `PolicyCheck`)
- `packages/nl2sql/src/nl2sql/policies/scope.py` (`PolicyEngine`, `Scope`)
- `packages/nl2sql/src/nl2sql/policies/check.py` (building a `policies.check` block)
- `packages/nl2sql/src/nl2sql/policies/patterns.py` (the pattern rules)
- `packages/nl2sql/src/nl2sql/config/models.py` (`Policies`, `Role`)

## Public Surface

### UserContext

Exported from `nl2sql`; defined in `nl2sql_sdk.policy`, so a policy check
needs only the SDK. Fields: `user_id` (optional), `tenant_id` (optional) and
`roles` (a list).

| method | returns | meaning |
| --- | --- | --- |
| `roles()` | `dict[str, Role]` | The configured roles by id: each one's `description`, `allow` and `deny` patterns. Empty with `allow_all`. |
| `scope(user_context, datasource_id)` | `Scope` | What `user_context` may read in `datasource_id`: the same `Scope` a run decides with. |
| `allows(user_context, datasource_id, table, schema=None)` | `bool` | Whether `user_context` may read `table`. `schema` defaults to the datasource's default schema. |
| `validate()` | `PolicyValidationReport` | Every pattern checked against the engine's datasources: one entry per pattern (`role`, `target` -- `Allow: <pattern>` or `Deny: <pattern>` --, `status` -- `OK`, `MISSING`, `UNAVAILABLE` -- and `details`), and `ok`. The patterns' own shape was checked when the config loaded. `nl2sql policy validate` prints it. |

### Scope

| method | returns | meaning |
| --- | --- | --- |
| `reaches_datasource()` | `bool` | Whether any role reaches the datasource (one of its allow patterns can match inside it) and, with a policy check, the check's grant does too. |
| `allows(schema, table)` | `bool` | Whether `schema.table` may be read; a `None` schema is the default schema. |
| `rule_for(schema, table)` | `str` or `None` | Why it may not be read (the deny pattern, or the missing allow, of a role or of the policy check), for the log; `None` when it may. |

`datasource_id` and `default_schema` are attributes.

```python
from nl2sql import UserContext

analyst = UserContext(roles=["analyst"])
engine.policies.allows(analyst, "shop", "customers")   # True
scope = engine.policies.scope(analyst, "shop")
scope.rule_for("main", "orders")   # "no allow pattern of role(s) 'analyst' matches 'shop.main.orders'"
engine.policies.roles()["analyst"].description
```

## Behavioral Contracts
- Patterns are `*`, `datasource` or `datasource.schema.table`, each part a
  glob, matched regardless of case; deny beats allow within a role, and roles
  add up. See [Policies](../../configuration/policies.md).
- Unknown roles grant nothing, and an empty `roles` list does too (unless
  `default_role` is set); neither raises. An unknown role beside a known one
  is ignored.
- `Scope` is the only access decision: the datasource resolver, the curated
  matcher, the schema retriever (which hides denied tables), the logical
  validator (which refuses), the executor's value check and `allows()` all
  ask it. Nothing outside `nl2sql/policies/` matches a pattern; the
  architecture test `test_every_access_decision_goes_through_scope` holds it.
- Inside a run, `PolicyEngine.scope` builds each datasource's scope once and
  returns the same one, so every step of the run decides the same way.
- With a policy check, the check's `Grant` is intersected with the roles, it
  is asked once per caller and datasource per run (and on every `scope()`
  call outside a run), and any exception from it denies the datasource. See
  [A policy check](../../configuration/policies.md#a-policy-check).
