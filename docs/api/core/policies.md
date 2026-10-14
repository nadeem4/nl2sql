# Policies API

## Purpose
`engine.policies`: who may read what. The roles, whether a caller may read a
table, what a caller may read, and whether the rules match the datasources.

## Responsibilities
- List the configured roles.
- Check one table for a user context, and list what a user context may read.
- Check that every rule names a datasource the engine has.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/policy_api.py` (`PoliciesAPI`, internal)
- `packages/nl2sql/src/nl2sql/auth/models.py` (`UserContext`)
- `packages/nl2sql/src/nl2sql/auth/rbac.py`
- `packages/nl2sql/src/nl2sql/config/models.py` (`Policies`, `Role`)

## Public Surface

### UserContext

Exported from `nl2sql`. Fields: `user_id` (optional), `tenant_id` (optional)
and `roles` (the RBAC roles, a list).

| method | returns | meaning |
| --- | --- | --- |
| `roles()` | `dict[str, Role]` | The configured roles by id: each one's `description`, `allowed_datasources` and `allowed_tables`. Empty with `allow_all`. |
| `allows(user_context, datasource_id, table, schema=None)` | `bool` | Whether `user_context` may read `table`. `schema` defaults to the datasource's default schema. |
| `allowed(user_context)` | `dict` | What `user_context` may read, as the rules say it: `{"datasources": [...], "tables": [...]}`. |
| `validate()` | `PolicyValidationReport` | Every datasource and table rule checked against the engine's datasources: one entry per rule (`role`, `target`, `status` -- `OK`, `MISSING`, `UNAVAILABLE`, `INVALID_DS` -- and `details`), and `ok`. The policies' own shape was checked when the config loaded. `nl2sql policy validate` prints it. |

```python
from nl2sql import UserContext

analyst = UserContext(roles=["analyst"])
engine.policies.allows(analyst, "shop", "customers")   # True
engine.policies.allowed(analyst)                       # {'datasources': ['shop'], 'tables': ['shop.customers']}
engine.policies.roles()["analyst"].description
```

## Behavioral Contracts
- Policies enforce table namespacing (`datasource.*`, `datasource.schema.*`,
  `datasource.schema.table`, or `datasource.table`, read as the datasource's
  default schema only). See [Policies](../../configuration/policies.md).
- Unknown roles yield empty permissions, and an empty `roles` list does too;
  neither raises. An unknown role beside a known one is ignored.
- `table_allowed(allowed_tables, datasource_id, table, schema, default_schema)`
  is the single matching rule used by the logical validator (to refuse), the
  schema retriever (to strip a forbidden table's statistics), the executor's
  value check and the curated matcher. `default_schema(ds_registry,
  datasource_id)` reads the default schema from the adapter; `main` stands in
  when the adapter cannot report one.
