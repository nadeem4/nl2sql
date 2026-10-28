# API keys

!!! note "Arrives with M1 (Wave C, 0.9)"
    This page is a stub. `engine.api_keys` and `nl2sql keys` are registered with their final signatures
    and name M1. The design is [Wave C, section 9](../architecture/wave_c.md#9-mcp-over-http-with-auth).

An API key authenticates a client of MCP over HTTP. A key carries the user, tenant and roles its holder
runs as; the token (`nl2sql_<id>_<secret>`) is shown once and only its hash is stored. Managing keys
needs an admin caller (`by`, required, no default); `verify` takes the token alone and returns the key's
`UserContext`, or `None` for a malformed, unknown or revoked one.

```text
nl2sql keys create --as admin --user ana --role analyst
nl2sql keys list --as admin
nl2sql keys revoke <id> --as admin
```