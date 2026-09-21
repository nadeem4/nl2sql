# Chinook Gold Evaluation Dataset

A set of about forty questions about the vendored Chinook database
(`nl2sql/cli/demo/data/chinook.sqlite`), each with hand-written SQL and the rows
that SQL returns. It is the reference an evaluation compares the engine's
answers against.

| File | What it holds |
| --- | --- |
| `packages/nl2sql/src/nl2sql/evaluation/datasets/chinook_gold.yaml` | The dataset |
| `packages/nl2sql/src/nl2sql/evaluation/gold.py` | `GoldQuestion` model, `load_gold_dataset()`, `execute_gold_sql()`, `regenerate()` |
| `packages/nl2sql/tests/unit/test_chinook_gold_dataset.py` | Key-free checks, part of the normal unit suite |

## Format

The file is a YAML list. Each entry:

| Field | Meaning |
| --- | --- |
| `id` | Unique id, `chinook_NNN` |
| `question` | The natural-language question |
| `difficulty` | `easy`, `medium` or `hard` |
| `tags` | Categories: `single-table`, `filter`, `join`, `multi-join`, `aggregation`, `having`, `order-limit`, `ties`, `date`, `paraphrase`, `keyword-in-question`, `keyword-in-value`, `rbac-denial`, `unanswerable` |
| `paraphrase_group` | Optional. Questions with the same intent share this id and the same `gold_result`, for determinism checks |
| `needed_tables` | Exactly the tables `gold_sql` reads, for retrieval recall |
| `needed_columns` | `Table.Column` names the answer needs, for retrieval recall |
| `expected` | Per demo role (`admin`, `analyst`, `viewer`): `allowed`, `refused` or `unanswerable` |
| `order_matters` | Whether a comparison should respect row order |
| `gold_sql` | Hand-written SQLite SQL, or `null` for an unanswerable question |
| `gold_result` | Rows `gold_sql` returns, as a list of `{column: value}` maps. Generated, never typed. `null` when `gold_sql` is `null` |

`expected` follows the demo policy in `nl2sql/cli/demo/chinook.py`
(`CHINOOK_POLICIES`): a role gets `refused` when any of `needed_tables` is
outside its `allowed_tables`, and `allowed` otherwise. Questions the database
cannot answer are `unanswerable` for every role. The `rbac-denial` tag marks
exactly the questions some role is refused.

Where `ORDER BY` has ties, `gold_sql` breaks them on a key (`CustomerId`,
`Country` and so on) so the result is deterministic, and the question carries
the `ties` tag. When the tie is only about order, `order_matters` is `false`.

## Regenerating the results

`gold_result` is always produced by running `gold_sql` against the database.
After adding or editing a question, set its `gold_result` to `null` (or leave
it stale) and run:

```bash
python -m nl2sql.evaluation.gold
```

It re-executes every `gold_sql`, rewrites the file in its canonical layout and
prints how many results it wrote. Commit the rewritten file.

## What the tests check

`pytest packages/nl2sql/tests/unit/test_chinook_gold_dataset.py` runs without
an API key and checks that:

- every entry validates against `GoldQuestion` and ids are unique;
- every `gold_sql` reproduces its committed `gold_result`, and the committed
  file is byte-for-byte what the generator writes;
- paraphrase groups share one result;
- every needed table and column exists, and `needed_tables` equals the tables
  in `gold_sql`;
- every role in `expected` exists in the demo policy and each outcome follows
  from it;
- the twelve demo questions (`CHINOOK_QUESTIONS`) are included verbatim.
