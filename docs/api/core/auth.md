# Auth API

## Purpose
Enforce role-based access control (RBAC) for datasource and table access.

## Responsibilities
- Check permissions for a user-context against policy rules.
- Return allowed datasources and tables for a user-context.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/auth_api.py`
- `packages/nl2sql/src/nl2sql/auth/models.py`
- `packages/nl2sql/src/nl2sql/auth/rbac.py`

## Public Surface

### UserContext

Source:
`packages/nl2sql/src/nl2sql/auth/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `user_id` | `Optional[str]` | no | Unique identifier for the user. |
| `tenant_id` | `Optional[str]` | no | Tenant/organization identifier. |
| `roles` | `List[str]` | no | Assigned RBAC roles. |

### RolePolicy

Source:
`packages/nl2sql/src/nl2sql/auth/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `description` | `str` | yes | Human-readable role description. |
| `role` | `str` | yes | Role ID for auditing/logging. |
| `allowed_datasources` | `List[str]` | no | Allowed datasource IDs or `*`. |
| `allowed_tables` | `List[str]` | no | Allowed tables: `*`, `datasource.*`, `datasource.schema.*`, `datasource.schema.table`, or `datasource.table` (default schema only). |

### AuthAPI.check_permissions

Source:
`packages/nl2sql/src/nl2sql/api/auth_api.py`

Signature:
`check_permissions(user_context: UserContext, datasource_id: str, table: str, schema: Optional[str] = None) -> bool`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `user_context` | `UserContext` | yes | User identity + roles. |
| `datasource_id` | `str` | yes | Datasource ID being accessed. |
| `table` | `str` | yes | Bare table name. |
| `schema` | `Optional[str]` | no | Schema of the table; `None` means the datasource's default schema. |

Returns:
`bool` indicating whether access is allowed.

Raises:
None directly. Policy validation errors can surface at load time.

Side Effects:
None.

Idempotency:
Yes.

### AuthAPI.get_allowed_resources

Source:
`packages/nl2sql/src/nl2sql/api/auth_api.py`

Signature:
`get_allowed_resources(user_context: UserContext) -> dict`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `user_context` | `UserContext` | yes | User identity + roles. |

Returns:
`dict` with keys `datasources` and `tables`.

Raises:
None.

Side Effects:
None.

Idempotency:
Yes.

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
