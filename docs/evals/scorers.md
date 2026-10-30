# Scorers

A scorer decides whether one case run passed. It is a registry kind: the
built-ins and any plugin's are listed the same way and named in
`evals.yaml`'s `scorers`.

| Kind | Entry point group | Interface | Version | Built-ins |
| --- | --- | --- | --- | --- |
| scorer | `nl2sql.scorers` | `nl2sql.evaluation.scorers.Scorer` | 1 | `result_match`, `outcome`, `retrieval_recall`, `faithfulness`, `routing` |

## The built-ins

| Scorer | Passes when | Applies to | Modes |
| --- | --- | --- | --- |
| `result_match` | The run's one terminal result (the combined result of a split question, not a sub-query's scan) matches the case's `rows` or any `alt_rows` | an `allowed` role on a case with `sql` | full, plans |
| `outcome` | The role got what the case says: an answer (`allowed`), the generic refusal (`refused`), `QUESTION_NOT_ANSWERABLE` (`unanswerable`) or status `needs_clarification` (`clarify`) | every role | full, plans |
| `retrieval_recall` | The schema retriever sent every one of the case's `tables` and `columns`; the value is the column recall | an `allowed` role with `tables` | full, retrieval |
| `faithfulness` | Every number and quoted name the answer states is in the result or its counts | a run that wrote an answer | full |
| `routing` | Mode full: the run used exactly the case's `datasources`. Mode retrieval: routing ranked one of them first (`top1`), and all of them are among the datasources it ranked, up to `settings.routing.max_datasources` (`shortlist`) | an `allowed` role | full, retrieval |

In mode retrieval, `routing` passes a case only on both `top1` and
`shortlist`. Its `detail` keeps each, with the ranking (`routed`) and the
datasources it `missed`, and the board's `routing` gives the share of cases
for each. The gate allows no flip in mode retrieval, so it fails when either
drops. A `kind: routing` case (which says where a question goes, not what it
returns) gets no `result_match` verdict and is left out of execution accuracy.

A scorer that does not apply to a case returns no verdict. A scorer that
cannot score the run's mode is left out of that run.

**A case passes when every scorer that applies to it passes.** A scorer that
raises scores the case as an **error**: its `passed` is `None` and the
message is in `detail["error"]`. The run goes on, and the report counts
errors apart from failures.

`result_match` scores the run's **terminal result**: what the aggregator saved, which for a question the decomposer split is
the combined result, not any sub-query's scan. A run with two terminal results fails with "expected one result set, got 2".
It compares rows strictly:

- column names are ignored, and so is column order: each gold column must be answered by a distinct result column
  with equal values, so a mislabelled column still fails;
- numbers (database decimals too) match within half a cent;
- dates match their ISO strings, and a date-only label matches that day's midnight in the other value's own offset;
- row order counts only when `order_matters`;
- the right rows with extra columns also pass, if the gold's columns appear among
  them, in any order, and match strictly.

`detail["match"]` says how the case passed: `strict` (same columns, same order),
`reordered` (the same columns in another order), `extra columns`, or
`extra columns, reordered`. A result of more than 12 columns is not searched for
reordered columns.

It also keeps the lenient reading in `detail["lenient"]`, which accepts
period labels against dates and extra columns beyond the cap besides. The
report gives the strict and lenient accuracy side by side. The rules, and
`faithfulness`'s, are in [the Chinook
suite](../testing/evaluation-dataset.md#strict-and-lenient-accuracy).

In mode full, `retrieval_recall` reads what the run's schema retriever sent.
In mode retrieval, the runner runs the retriever itself, with the question as
the query.

## The interface

```python
# nl2sql/evaluation/scorers/__init__.py (engine side: a scorer sees a QueryResult)
class Score(BaseModel):
    value: float | None        # 0..1; None when the scorer does not apply to this case
    passed: bool | None
    detail: dict[str, Any] = {}

class CaseRun(BaseModel):      # what a scorer is given
    case: EvalCase
    role: str
    mode: str                  # "full", "plans" or "retrieval"
    result: QueryResult        # the run's result, with its sub-queries
    sets: list[RowSample]      # the terminal result sets, every row (the combined result of a split question)
    rows: list[list[Any]] | None     # the first terminal result set's rows
    columns: list[str]
    retrieval: dict[str, list[str]] | None   # table -> columns the retriever sent
    routed: list[str] | None                 # mode retrieval: datasources, best first

class Scorer(Protocol):
    interface_version: ClassVar[int]          # 1
    config_model: ClassVar[type[BaseModel]]
    modes: ClassVar[frozenset[str]]           # the modes it can score
    @classmethod
    def from_config(cls, cfg: BaseModel) -> "Scorer": ...
    def score(self, run: CaseRun) -> Score: ...
```

`CaseRun.expected` is the case's outcome for the run's role. Each scorer is
built once per run, from its `config_model`'s defaults.

## Writing your own

A scorer that fails any answer slower than five seconds:

```python
from nl2sql import NL2SQL, Registry, Score
from nl2sql.evaluation.scorers import NoConfig


class Fast:
    interface_version = 1
    config_model = NoConfig
    modes = frozenset({"full"})

    @classmethod
    def from_config(cls, cfg):
        return cls()

    def score(self, run):
        if run.expected != "allowed":
            return Score()                        # does not apply
        seconds = sum(run.result.timings.values())
        return Score(value=1.0 if seconds <= 5 else 0.0, passed=seconds <= 5,
                     detail={"seconds": seconds, "reason": f"took {seconds:.1f} s"})


registry = Registry.default()
registry.add_scorer("fast", Fast)
engine = NL2SQL(config, registry=registry)
engine.evals.run(scorers=["result_match", "outcome", "fast"], max_cost_usd=2)
```

A plugin package declares it as an entry point instead:

```toml
[project.entry-points."nl2sql.scorers"]
fast = "my_package.scorers:Fast"
```

Put a `reason` in `detail` when a case fails. The report shows the first
failing scorer's reason for the case.
