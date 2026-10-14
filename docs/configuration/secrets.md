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
- `type`: `aws`, `azure`, `hashicorp`, or `env`

Every other key is the provider's own setting:

| `type` | Settings | Extra to install |
| --- | --- | --- |
| `aws` | `region_name`, `profile_name`, both optional (boto3's own lookup fills them in) | `nl2sql-engine[aws]` |
| `azure` | `vault_url` (required), `client_id`, `client_secret`, `tenant_id`. Without all three credentials the provider signs in with `DefaultAzureCredential`. | `nl2sql-engine[azure]` |
| `hashicorp` | `url` and `token` (both required), `mount_point` (default `secret`) | `nl2sql-engine[hashicorp]` |
| `env` | none | none |

```yaml
version: 1
providers:
  - id: vault
    type: hashicorp
    url: "https://vault.internal:8200"
    token: "${env:VAULT_TOKEN}"
```

The settings are strict. An unknown or misspelt key, or a missing required
one, fails when the config loads, and the message names the key.

A provider reads its settings from its entry only. It never falls back to an
environment variable such as `VAULT_ADDR`, `VAULT_TOKEN` or
`AZURE_KEYVAULT_URL`: when a value comes from the environment, write the
field as an `${env:...}` reference, as `token` does above.

The HashiCorp key is the secret's path and, after a colon, the field:
`${vault:apps/sales:password}` reads the field `password` of `apps/sales` in
the KV version 2 engine at `mount_point`. Without a colon it reads the field
`value`.

Each provider implements the `SecretProvider` interface from the SDK; see
[SDK reference → SecretProvider](../adapters/sdk.md#interface-secretprovider).

## Secret references

Use `${provider_id:key}` in config files to resolve a secret from a provider.
Use `${env:VAR}` to read directly from environment variables.

## When a provider fails to start

A provider listed in `secrets.yaml` that cannot start fails startup at once
(the engine, `nl2sql-api`, and `nl2sql doctor`, which prints it as
"Connectivity check failed"). The error names the provider and the reason:

```text
Secret provider 'vault' (type 'hashicorp') failed to start: Missing dependency 'hvac'.
Install 'nl2sql-engine[hashicorp]'. Fix or remove this entry in the secrets config.
```

The usual reasons are the provider's extra not being installed
(`nl2sql-engine[aws]`, `[azure]`, `[hashicorp]`), a `${env:VAR}` credential whose
variable is not set (the message names the variable), or the provider's own
client refusing the config. Credentials are masked in the message.

This is deliberate: every `${vault:...}` reference depends on the provider, so
starting without it only moves the error to the first lookup, where it reads
as "Unknown secret provider ID". A missing or empty `secrets.yaml` and the
built-in `env` provider are unaffected. A datasource that fails to start, by
contrast, leaves the others running: see
[Datasources → When a datasource fails to start](datasources.md#when-a-datasource-fails-to-start).

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
