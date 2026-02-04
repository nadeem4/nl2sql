# Auth API

## Purpose
Enforce role-based access control (RBAC) for datasource and table access.

## Responsibilities
- Check permissions for a user-context against policy rules.
- Return allowed datasources and tables for a user-context.

## Key Modules
- `packages/core/src/nl2sql/api/auth_api.py`
- `packages/core/src/nl2sql/auth/models.py`
- `packages/core/src/nl2sql/auth/rbac.py`

## Public Surface

### UserContext

Source:
`packages/core/src/nl2sql/auth/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `user_id` | `Optional[str]` | no | Unique identifier for the user. |
| `tenant_id` | `Optional[str]` | no | Tenant/organization identifier. |
| `roles` | `List[str]` | no | Assigned RBAC roles. |

### RolePolicy

Source:
`packages/core/src/nl2sql/auth/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `description` | `str` | yes | Human-readable role description. |
| `role` | `str` | yes | Role ID for auditing/logging. |
| `allowed_datasources` | `List[str]` | no | Allowed datasource IDs or `*`. |
| `allowed_tables` | `List[str]` | no | Allowed tables in `datasource.table` format. |

### AuthAPI.check_permissions

Source:
`packages/core/src/nl2sql/api/auth_api.py`

Signature:
`check_permissions(user_context: UserContext, datasource_id: str, table: str) -> bool`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `user_context` | `UserContext` | yes | User identity + roles. |
| `datasource_id` | `str` | yes | Datasource ID being accessed. |
| `table` | `str` | yes | Table name (`datasource.table` format enforced by policy). |

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
`packages/core/src/nl2sql/api/auth_api.py`

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
- Policies enforce table namespacing (`datasource.table` or `datasource.*`).
- Unknown roles yield empty permissions.
