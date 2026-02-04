# Secrets Configuration

Secrets configuration is optional and lives in `configs/secrets.yaml`. It defines
secret providers that can be referenced in other config files.

## File structure

```yaml
version: 1
providers:
  - id: aws-main
    type: aws
    region_name: "us-east-1"
```

## Providers

Each provider has:

- `id`: unique provider ID used in secret references (e.g. `${aws-main:db_password}`)
- `type`: `aws`, `azure`, `hashi`, or `env`

Provider-specific fields:

- **aws**: `region_name`, `profile_name`
- **azure**: `vault_url`, `client_id`, `client_secret`, `tenant_id`
- **hashi**: `url`, `token`, `mount_point`
- **env**: no additional fields

## Secret references

Use `${provider_id:key}` in config files to resolve a secret from a provider.
Use `${env:VAR}` to read directly from environment variables.
