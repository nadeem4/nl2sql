# Launch

!!! note "Arrives with L3 (Wave C, 0.9)"
    This page is a stub. `nl2sql launch` is registered with its final options and exits 1 naming L3.
    The design is [Wave C, section 7](../architecture/wave_c.md#7-launchpad-the-setup-workflow).

`nl2sql launch [DIR]` takes a database from "I have a file or a connection string" to a first answer: it
finds candidates (`engine.datasources.scan`), asks for what is missing, tests the connection, writes the
project (secrets to `.env`, never to the YAML), indexes, profiles, and files starter questions for review.

| Option | Meaning |
|---|---|
| `--provider NAME` | The LLM provider and its default model |
| `--as ROLE` | The admin role the launch acts as (default: the first of `policies.admin_roles`) |
| `--draft` | Also draft descriptions with the model, under the profiler's cost cap |
| `--yes` | Accept nothing: export the review file for review in git |
| `--no-ports` | Do not probe local database ports |

`launch` is the one command that reads `DATABASE_URL`.