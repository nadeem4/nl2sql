import concurrent.futures
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from nl2sql.api.query_api import QueryResult, result_from_state
from nl2sql.auth import UserContext
from nl2sql.context import NL2SQLContext
from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql.evaluation.gold import GoldQuestion, load_gold_dataset
from nl2sql.evaluation.types import BenchmarkConfig
from nl2sql.pipeline.runtime import run_with_graph

# Every row is compared, so the sample is never the thing that caps a result.
# The largest gold result has 25 rows; the datasource row limit applies anyway.
_ALL_ROWS = 100_000


@dataclass
class BenchmarkResult:
    """Standardized result object from a benchmark run."""

    results: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    iterations: int = 1


class BenchmarkRunner:
    """Runs each gold question once per role through the pipeline and scores it.

    ``before_case`` is called with the question just before each run; the
    tier 1 harness uses it to tell its fake LLM which gold plan to serve,
    which is also why it runs with ``workers=1``.
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        ctx: NL2SQLContext,
        *,
        workers: int = 5,
        before_case: Optional[Callable[[GoldQuestion], None]] = None,
    ):
        self.config = config
        self.ctx = ctx
        self.workers = workers
        self.before_case = before_case

    def run_dataset(self, progress_callback=None) -> BenchmarkResult:
        """Runs every (question, role) case ``iterations`` times and scores it."""
        cases = self._cases()
        iterations = self.config.iterations or 1
        jobs = [case for _ in range(iterations) for case in cases]

        if self.workers <= 1:
            iterator = (self._evaluate_case(q, role) for q, role in jobs)
            if progress_callback:
                iterator = progress_callback(iterator, total=len(jobs), description="Evaluating...")
            results = list(iterator)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = [executor.submit(self._evaluate_case, q, role) for q, role in jobs]
                iterator = concurrent.futures.as_completed(futures)
                if progress_callback:
                    iterator = progress_callback(
                        iterator, total=len(jobs),
                        description=f"Evaluating ({self.workers} parallel, {iterations} runs)...",
                    )
                results = [f.result() for f in iterator]

        results.sort(key=lambda r: (r["id"], r["role"]))
        return BenchmarkResult(results=results, metrics=ModelEvaluator.summarize(results), iterations=iterations)

    def cases(self) -> List[Tuple[GoldQuestion, str]]:
        """Every (question, role) pair this runner would run, in dataset order."""
        return self._cases()

    def _cases(self) -> List[Tuple[GoldQuestion, str]]:
        """Every (question, role) pair selected by ``include_ids`` and ``roles``."""
        dataset = load_gold_dataset(self.config.dataset_path)
        if self.config.include_ids:
            dataset = [q for q in dataset if q.id in self.config.include_ids]
            if not dataset:
                raise ValueError(f"No test cases found matching IDs: {self.config.include_ids}")
        return [
            (q, role)
            for q in dataset
            for role in q.expected
            if not self.config.roles or role in self.config.roles
        ]

    def _run(self, question: GoldQuestion, role: str) -> QueryResult:
        state = run_with_graph(self.ctx, question.question, user_context=UserContext(roles=[role]))
        return result_from_state(state, artifact_store=getattr(self.ctx, "artifact_store", None),
                                 sample_rows=_ALL_ROWS)

    def _evaluate_case(self, question: GoldQuestion, role: str) -> Dict[str, Any]:
        """Runs one case and returns its report row."""
        return self.run_case(question, role)[0]

    def run_case(self, question: GoldQuestion, role: str) -> Tuple[Dict[str, Any], Optional[QueryResult]]:
        """Runs and scores one case: its report row, and the result (None if the run raised)."""
        expected = question.expected[role]
        row: Dict[str, Any] = {"id": question.id, "question": question.question, "role": role,
                               "expected": expected, "status": "", "reason": "", "sql": "", "rows": None,
                               "gold_rows": len(question.gold_result or [])}
        if self.before_case:
            self.before_case(question)
        try:
            result = self._run(question, role)
        except Exception as exc:  # one broken case must not abort the run
            return {**row, "status": "fail", "reason": f"run raised {type(exc).__name__}: {exc}"}, None

        status, reason = ModelEvaluator.score_case(question, role, result)
        samples = [sq.rows for sq in result.sub_queries if sq.rows is not None]
        return {**row, "status": status, "reason": reason,
                "sql": result.sub_queries[0].sql if result.sub_queries else "",
                "rows": samples[0].total_rows if samples else None}, result
