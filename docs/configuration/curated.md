# Curated Answers (`curated.yaml`)

`curated.yaml` is where a data team writes **verified queries**: SQL they have
already written and reviewed, with typed parameters, and the questions each
query answers.

!!! note "What works today"
    This release loads and checks the file: at startup and with
    `nl2sql curated validate`. Matching a question to a verified query and
    running it comes in a later release; until then the file changes no
    answer.

## Where the file lives

| Setting | Env | Default | Meaning |
| --- | --- | --- | --- |
| `curated_config_path` | `CURATED_CONFIG` | `configs/curated.yaml` | The curated answers file, relative to the working directory or absolute. |
| `curated_enabled` | `CURATED_ENABLED` | `true` | `false` turns the feature off without removing the file. `nl2sql curated validate` still checks it. |

The file is optional:

- `CURATED_CONFIG` left at its default and no `configs/curated.yaml`: the
  feature is off, and nothing else changes.
- `CURATED_CONFIG` set to any other path that does not exist: startup fails
  with `FileNotFoundError`. A typo should not quietly turn verified answers off.
- The file exists but is broken: startup (`NL2SQL(...)`, `nl2sql run`, the
  REST API, the playground) fails with a `ValueError` that names every problem,
  as a broken `datasources.yaml` does.

## Writing the file

One file covers every datasource, keyed by the datasource `id` from
`datasources.yaml`. This is the Chinook example from
[`configs/curated.example.yaml`](https://github.com/nadeem4/nl2sql/blob/main/configs/curated.example.yaml):

```yaml
version: 1
datasources:
  chinook:
    verified_queries:
      - id: top_customers_by_revenue
        description: "Customers ranked by invoiced revenue in a calendar year"
        questions:
          - "Who are our top {n} customers by revenue in {year}?"
          - "Best {n} customers in {year}"
        params:
          n:    { type: int, default: 10, min: 1, max: 100 }
          year: { type: int, min: 2000, max: 2100, example: 2012 }
        sql: |
          SELECT c.CustomerId,
                 c.FirstName || ' ' || c.LastName AS customer,
                 c.Country,
                 ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue
          FROM Customer c
          JOIN Invoice i      ON i.CustomerId = c.CustomerId
          JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
          WHERE CAST(strftime('%Y', i.InvoiceDate) AS INTEGER) = :year
          GROUP BY c.CustomerId, customer, c.Country
          ORDER BY revenue DESC, c.CustomerId
          LIMIT :n
    metrics: []
```

### A verified query

| Key | Required | Meaning |
| --- | --- | --- |
| `id` | yes | A slug: lower-case letters, digits and `_`, starting with a letter. Unique within its datasource. |
| `description` | yes | What the query returns, in a sentence. |
| `questions` | yes, at least one | Phrasings the query answers. `{name}` marks where a parameter goes. |
| `params` | no | The parameters, by name. |
| `sql` | yes | The query, in the datasource's own SQL dialect. |

### A parameter

| Key | Meaning |
| --- | --- |
| `type` | `int`, `float`, `string` or `date` (`YYYY-MM-DD`). |
| `default` | The value used when the question gives none. A param with no default is required. |
| `min`, `max` | Bounds, for `int`, `float` and `date`. |
| `choices` | The allowed values, for `string`. |
| `example` | A typical value, used where a value is needed and there is no default. |

### The SQL

- Write it in the dialect of the datasource it belongs to: the SQLite example
  above uses `strftime`, a Postgres datasource would use `EXTRACT`. It is
  parsed with the dialect the datasource's adapter declares.
- Exactly one statement, and a query: a `SELECT`, a set operation such as
  `UNION`, or a `WITH` around one. No `INSERT`, `UPDATE`, `DELETE`, `MERGE`,
  `CREATE`, `DROP`, `ALTER`, `SELECT ... INTO` or statements the parser cannot
  type (`VACUUM`, `PRAGMA`), anywhere in the tree, a CTE included.
- Parameters are `:name` placeholders, and they are exactly the declared
  `params`: no placeholder without a param, no param the SQL does not use.
  Positional `?` placeholders are refused. Values are always bound, never
  pasted into the text, so a value cannot change the shape of the query.

### Metrics

`metrics:` (a list of `name`, `description`, `expression`, `table`) is
accepted and checked for shape so the format is fixed, but nothing uses it yet.

Every key is checked: a misspelt one (`descripton:`) is an error, not ignored.

## Checking the file

```bash
nl2sql curated validate
nl2sql curated validate --curated configs/curated.yaml --config configs/datasources.yaml
```

| Option | Default |
| --- | --- |
| `--curated` | `CURATED_CONFIG` |
| `--config` | `DATASOURCE_CONFIG` |
| `--secrets` | `SECRETS_CONFIG` |

It runs every check startup runs, plus one that needs the index: every table
a query reads (CTE names aside) is in its datasource's latest indexed schema,
compared case-insensitively. A datasource that has not been indexed yet is
skipped for that check, with a note. The command prints a table, one row per
query, and exits 1 on any error, so it can run in CI:

```text
                         Verified Queries
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Datasource ┃ Query                    ┃ Status ┃ Details                            ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ chinook    │ top_customers_by_revenue │ OK     │ Verified against the indexed schema │
└────────────┴──────────────────────────┴────────┴────────────────────────────────────┘
```

| Check | At startup | `curated validate` |
| --- | --- | --- |
| YAML parses and every key is known | yes | yes |
| Every datasource key is a configured datasource | yes | yes |
| Query ids unique within a datasource | yes | yes |
| Every `{placeholder}` in a question is a param, every param is in a question | yes | yes |
| Defaults, examples, bounds and choices agree with the type | yes | yes |
| SQL is one read-only query in the datasource's dialect | yes | yes |
| SQL `:name` placeholders are exactly the params | yes | yes |
| Tables the SQL reads are in the indexed schema | no | yes, when indexed |

From Python, the same check is `CuratedAPI`, also available as
`NL2SQL(...).curated`:

```python
from nl2sql import CuratedAPI

report = CuratedAPI().validate()          # or validate(curated_path=..., datasources_path=...)
if not report.ok:
    for error in report.errors:
        print(error)
    for entry in report.entries:
        print(entry.datasource_id, entry.query_id, entry.status, entry.details)
```

## Source references

- Models and SQL checks: `packages/nl2sql/src/nl2sql/configs/curated.py`
- Loader: `ConfigManager.load_curated()` in `packages/nl2sql/src/nl2sql/configs/manager.py`
- Startup load: `packages/nl2sql/src/nl2sql/context.py`
- Facade: `packages/nl2sql/src/nl2sql/api/curated_api.py`
- Command: `packages/nl2sql/src/nl2sql/cli/commands/curated.py`
