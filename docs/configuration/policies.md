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
  - `allowed_tables`: list of tables in `datasource.table` format

## Table wildcards

- `*` allows all tables
- `datasource.*` allows all tables in a datasource
