"""``engine.evals``: evaluation as a product (wave_a.md, section 8, "The facade").

``load`` reads the suites ``evals.yaml`` names, ``run`` runs them in one of
three modes and returns an ``EvalReport``, ``regenerate`` writes a suite's
expected rows through the datasource's adapter, and ``scorers`` lists the
registered scorer names, and ``gate`` compares a report with a baseline
(the regression gate). Seeding (``seed``) arrives with V3. This module never imports ``nl2sql.evaluation``
at module scope: building an engine never loads it.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

if TYPE_CHECKING:
    from nl2sql.config import LLM
    from nl2sql.evaluation.gate import GateResult
    from nl2sql.evaluation.regenerate import RegenerateReport
    from nl2sql.evaluation.report import EvalReport
    from nl2sql.evaluation.suite import EvalSuite


class EvalsAPI:
    """The engine's evals, configured by the ``evals`` section. Internal: reach it through ``NL2SQL.evals``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def _path(self, path: Union[str, Path]) -> Path:
        path = Path(path)
        folder = self._ctx.config.folder
        return path if path.is_absolute() or folder is None else Path(folder) / path

    def load(self, path: Optional[Union[str, Path]] = None) -> List["EvalSuite"]:
        """The suites named in ``evals.yaml`` (relative to ``nl2sql.yaml``), or the one file at ``path``.

        Raises:
            ValueError: no ``path`` and no suite configured.
        """
        from nl2sql.evaluation.suite import load_suite

        if path is not None:
            return [load_suite(self._path(path))]
        suites = self._ctx.config.evals.suites
        if not suites:
            raise ValueError("No eval suite is configured. Name one in evals.yaml (suites: [evals/mine.yaml]), "
                             "or pass a suite file.")
        return [load_suite(self._path(p)) for p in suites]

    def run(
        self,
        suites: Optional[Sequence[Union[str, Path, "EvalSuite"]]] = None,
        *,
        mode: str = "full",
        llm: Union[None, "LLM", Mapping[str, Any]] = None,
        cases: Optional[Sequence[str]] = None,
        roles: Optional[Sequence[str]] = None,
        scorers: Optional[Sequence[str]] = None,
        curated: Optional[bool] = None,
        passes: int = 1,
        max_cost_usd: Optional[float] = None,
        on_case: Optional[Callable[[str, Dict[str, Any], float], None]] = None,
        clarify: bool = False,
    ) -> "EvalReport":
        """Run every approved case, once per role in its ``outcome`` map, ``passes`` times.

        Args:
            suites: Suites or suite files; ``None`` runs those ``evals.yaml`` names.
            mode: ``full`` (the pipeline on the configured LLM; needs a cost cap),
                ``plans`` (each case's gold plan on a fake LLM; no key) or
                ``retrieval`` (routing and schema retrieval only; no model).
            llm: Mode full: ``None`` (the engine's own ``llm`` section), one
                ``LLM``, or a mapping of names to ``LLM`` sections, each run on
                its own derived runtime over the same cases and compared.
            cases: Case ids or tags to run; ``None`` runs every approved case.
            roles: Roles to run; ``None`` runs every role in each case's outcome.
            scorers: Scorer names; ``None`` takes ``evals.yaml``'s.
            curated: Mode full: whether curated answers are on; ``None`` takes
                ``evals.yaml``'s (on by default). Modes plans and retrieval
                always run with them off.
            passes: How many times each case runs.
            max_cost_usd: Mode full's dollar cap over the whole run; ``None``
                takes ``evals.yaml``'s, and mode full needs one.
            on_case: Called after each case with the config's name, the case
                record and the dollars spent so far.
            clarify: Run the cases whose outcome is ``clarify``. Off by default;
                Wave B's clarification is what such a case expects.

        Returns:
            The ``EvalReport``: one board per LLM config.
        """
        from nl2sql.evaluation import run as runner
        from nl2sql.evaluation.records import database_identity
        from nl2sql.evaluation.suite import EvalSuite

        cfg = self._ctx.config.evals
        loaded: List[EvalSuite] = []
        for suite in suites if suites is not None else [None]:
            if suite is None:
                loaded.extend(self.load())
            else:
                loaded.extend([suite] if isinstance(suite, EvalSuite) else self.load(suite))
        report = runner.run_evals(
            self._ctx, loaded, mode=mode, llm=llm, cases=cases, roles=roles,
            scorers=list(scorers if scorers is not None else cfg.scorers),
            curated=cfg.curated if curated is None else curated, passes=passes,
            max_cost_usd=max_cost_usd if max_cost_usd is not None else cfg.max_cost_usd,
            on_case=on_case, clarify=clarify)
        if mode == "retrieval" and getattr(self._ctx, "embedding_info", None) is not None:
            # Recall depends on the embedder: a record names the one that measured it.
            info = self._ctx.embedding_info
            report.settings.setdefault("retrieval", {})["embedding"] = f"{info.provider}/{info.model}"
        datasources = sorted({d for s in loaded for c in s.approved() for d in c.datasources})
        if hasattr(self._ctx, "vector_store"):
            report.database = database_identity(self._ctx, datasources)
        return report

    def gate(self, report: "EvalReport", baseline: Any) -> "GateResult":
        """``report`` against ``baseline``, with ``evals.yaml``'s ``gate`` settings.

        ``baseline`` is another ``EvalReport`` (or its JSON), a record or a list
        of records from ``benchmarks/``, or a path: a report or record file, or
        a ``benchmarks/`` folder, whose newest record of the report's mode,
        database and config is the baseline. A relative path is relative to the
        working directory. Only runs of the same suite digest and mode are
        compared, on execution accuracy (``result_match`` and ``outcome``), as
        users see it and on the generated path; see ``nl2sql.evaluation.gate``.
        """
        from nl2sql.evaluation import gate

        if isinstance(baseline, (str, Path)):
            baseline = gate.load_baseline(baseline, report)
        settings = self._ctx.config.evals.gate
        return gate.gate(report, baseline, max_regressions=settings.max_regressions, alpha=settings.alpha,
                         max_cost_increase=settings.max_cost_increase)

    def regenerate(self, path: Union[str, Path], *, allow_packaged: bool = False) -> "RegenerateReport":
        """Run each approved case's ``sql`` and ``alt_sql`` through its datasource's adapter and write the rows.

        A suite the package ships is refused unless ``allow_packaged`` (the
        repo's own workflow, from a checkout).
        """
        from nl2sql.evaluation.regenerate import regenerate

        return regenerate(self._ctx.ds_registry, self._path(path), allow_packaged=allow_packaged)

    def scorers(self) -> List[str]:
        """The registered scorer names: the built-ins and every plugin's."""
        return self._ctx.registry.names("scorer")
