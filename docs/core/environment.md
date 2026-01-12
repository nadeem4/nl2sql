# Environment Management

The platform employs a **Universal Environment Protocol** to manage configuration across Development, Demo, and Production.

## .env Files

Configuration is loaded from `.env.{environment}` files. The global `.env` is loaded first, followed by the specific environment file which overrides values.

* `dev`: `.env.dev`
* `demo`: `.env.demo`
* `prod`: `.env.prod`

### Generating Environments

The CLI provides a generator to create strict, compliant environment files:

```bash
nl2sql setup --env prod
```

This ensures required keys (like `OPENAI_API_KEY`, `SecretProvider` configs) are present.

### Secrets Injection

Secrets are dynamically injected during the build process or runtime using the `SecretManager`. References in the `.env` file can use the `${provider:key}` syntax.

Example `.env.prod`:

```ini
# Database Connection
DB_PASSWORD=${aws-secrets:prod-db-password}
API_KEY=${env:OPENAI_API_KEY}
```

::: nl2sql.cli.generators.env.generator.EnvFileGenerator
