# nl2sql-cli

The `nl2sql` command for [NL2SQL](https://github.com/nadeem4/nl2sql): set up a project, index its schema, ask questions, run evaluations and review what the engine learned. It is a thin layer over the public `nl2sql` facade of `nl2sql-engine`.

```bash
pip install "nl2sql-cli[web]"   # the command and the web app
nl2sql demo                     # a demo project in ./nl2sql-demo, served at http://127.0.0.1:8765/
```

Without the `web` extra, `pip install nl2sql-cli` gives the command alone (every command except `nl2sql demo`, which starts the web app, `nl2sql-api`, as a child process).

The distribution depends on `nl2sql-engine` and `textual` only. It is imported as `nl2sql_cli` and installs one console script, `nl2sql`. The engine and the web app never import it.

Documentation: <https://nl2sql.codewithnk.com> and the repository's `docs/`.
