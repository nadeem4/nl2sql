# Security policy

## Reporting a vulnerability

Report it privately. Do not open a public issue, discussion or pull request for it.

Use GitHub's private vulnerability reporting: on the repository, open
**Security**, then **Report a vulnerability**
(<https://github.com/nadeem4/nl2sql/security/advisories/new>). Include the
affected package and version, what an attacker can do, and the steps or a
minimal example that reproduce it. Never include a real API key or real data.

You can expect an acknowledgement within a few days. A confirmed vulnerability
is fixed in a new release, and the advisory credits you unless you prefer
otherwise.

## Supported versions

Only the latest minor release of `nl2sql-sdk`, `nl2sql-engine`, `nl2sql-api` and
`nl2sql-cli` (the four share one version) receives security fixes.

## What is in scope

How the engine treats the model's output, the caller's role and the database is
described in the [Security Model](https://nadeem4.github.io/nl2sql/latest/security/model/),
including what it does not do (for example, it does not authenticate callers
itself, and it does not make a database connection read-only). A report that
breaks a guarantee that page makes is in scope; one that depends on a
deployment ignoring the advice on that page is usually not.
