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

`providers` may be omitted, left empty, or have every entry commented out. All
three mean "no secret providers configured" and load without error, which is
why the shipped `configs/secrets.yaml` is usable as-is.

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

## Secrets never print

Credentials are held as pydantic `SecretStr`, so `repr`, `str`, log lines and
tracebacks show `**********` instead of the value:

- the settings `OPENAI_API_KEY` and `RESULT_ARTIFACT_ADLS_CONNECTION_STRING`;
- `api_key` in the LLM config (`AgentConfig`), including `model_dump(mode="json")`
  and `model_dump_json()`;
- `client_secret` (Azure) and `token` (HashiCorp) in `secrets.yaml`;
- datasource connection fields whose name contains `password`, `secret`,
  `token` or `api_key` (masked in the connection config's `repr`/`str`).

The real value is read only where it is used: building the LLM or embeddings
client, a database connection, the artifact store's storage options or a
secret provider's client. Writers of config files (`nl2sql setup`, the demo
scaffold, the playground's Settings page) still write the literal value or the
`${env:...}` reference, never the mask.
