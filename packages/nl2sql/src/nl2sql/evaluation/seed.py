"""Seeding: draft eval cases from a datasource, for a person to review (wave_a.md, section 8; D26).

``engine.evals.seed("sales", count=20, out="evals/sales.yaml", user_context=...)``:

1. **As the caller.** Seeding needs a caller with a role, and fails closed: no
   caller, no role, or a role whose scope does not reach the datasource is
   refused before anything is asked. The ``eval_seeder`` agent is shown the
   datasource's live build exactly as the planner would show it to that
   caller (``SchemaRetrieverNode.visible_tables``): a table the role may not
   read never reaches the model.
2. **Questions and typed plans, never SQL,** :data:`BATCH_SIZE` per call, until
   ``count`` are written or the budget is spent. Each call goes through
   parse-and-retry, as every structured call does.
3. **Under its own budget.** Seeding is not a question, so it runs under a
   :class:`~nl2sql.limits.Budget` of its own: ``max_cost_usd`` (by default
   ``evals.yaml``'s, and required), and at most :data:`CALLS_PER_BATCH` calls
   per batch, so a model that keeps answering nothing usable stops. A model
   the price table cannot price refuses to start, as mode full does. When the
   budget is spent, the drafts so far are written and the report says so.
4. **Validation before generation.** Each plan is run as a pinned plan
   (``run_query(plan=SuppliedPlan(...), execute=False)``) under the caller's
   scope: ``plan.after``, the logical validator and the generator, with no
   model call. A plan the validator rejects is dropped and counted.
5. **Read-only through the adapter.** The SQL runs through the datasource's
   adapter, as ``regenerate`` runs a suite's SQL: a read-only session with
   the datasource's timeout, the row limit in the SQL the generator wrote. A
   failure or an empty result drops the case. No row is kept.
6. **Drafts, appended.** Survivors are appended to ``out`` as ``status:
   draft``, ``origin: seeded``, with the question, the engine's SQL, the plan,
   the tables and columns the plan reads, and ``outcome`` the caller's roles
   allowed. ``rows`` stay empty: ``regenerate`` writes them once a person
   approves the case. A question already in the file is skipped. Drafts never
   go into a suite the package ships, a suite ``evals.yaml`` names, or a file
   holding approved cases.
"""
from __future__ import annotations

import math
import pathlib
from typing import Any, Dict, List, Optional, Sequence, Set

from nl2sql_sdk.contracts import AdapterRequest
from pydantic import BaseModel, Field, ValidationError

from nl2sql.common.dates import run_date
from nl2sql.common.logger import get_logger
from nl2sql.evaluation.prompts import EVAL_SEEDER_PROMPT
from nl2sql.evaluation.run import UnknownPriceError
from nl2sql.evaluation.suite import SUITES_DIR, Difficulty, EvalCase, file_header, holds_approved_cases, \
    load_suite, write_suite
from nl2sql.limits import Budget, LimitReached
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, PlanModel
from nl2sql.services.callbacks.token_handler import QuestionUsage

logger = get_logger(__name__)

SEED_AGENT = "eval_seeder"
# Questions asked for per call: twenty plans in one structured answer would be
# fragile to parse, and one bad plan would cost the other nineteen.
BATCH_SIZE = 5
# Model calls allowed per batch, parse retries included, before seeding gives up.
CALLS_PER_BATCH = 3
# Where the budget's messages say the cap is set.
SETTING = "evals"

HEADER = (
    "# Draft eval cases seeded by `nl2sql eval seed` from the datasource's schema.\n"
    "# Read each one, fix the question or the SQL, then set status: approved and run\n"
    "# `nl2sql eval regenerate` on this file to write its rows. Drafts never run in an eval.\n"
)


class SeedCase(BaseModel):
    """One question the model proposes, with the plan that answers it."""

    question: str = Field(description="The question, in plain language, as a business user would ask it.")
    difficulty: Difficulty
    plan: PlanModel


class SeedBatch(BaseModel):
    """The ``eval_seeder`` agent's answer: a batch of questions, each with its plan."""

    cases: List[SeedCase] = Field(default_factory=list)


class SeedReport(BaseModel):
    """What seeding wrote, what it dropped and why, whether the cost cap stopped it, and its model usage."""

    path: pathlib.Path
    written: int = 0
    dropped_invalid: int = 0     # the plan did not parse, or the validator (or a hook) refused it
    dropped_failed: int = 0      # the database rejected the SQL
    dropped_empty: int = 0       # the SQL returned no rows
    dropped_duplicate: int = 0   # the question is already in the file, or was proposed twice
    stopped_at_cost_cap: bool = False
    usage: QuestionUsage = Field(default_factory=QuestionUsage)


def check_target(path: pathlib.Path, configured: Sequence[pathlib.Path]) -> None:
    """Refuse a file drafts must never go into, as ``feedback export`` does.

    Raises:
        ValueError: ``path`` is a suite the package ships, one of ``configured``
            (the suites ``evals.yaml`` names), or a file holding approved cases.
    """
    target = pathlib.Path(path).resolve()
    if target.parent == SUITES_DIR.resolve():
        raise ValueError(f"{path} is a suite the package ships; drafts never go into it. Write them to another file.")
    if target in {pathlib.Path(p).resolve() for p in configured}:
        raise ValueError(f"{path} is a suite evals.yaml names; drafts never go into it. Write them to another file.")
    if holds_approved_cases(path):
        raise ValueError(f"{path} holds approved cases; drafts never go into it. Write them to another file.")


def _key(question: str) -> str:
    return " ".join(question.lower().split())


def _columns(plan: PlanModel) -> List[str]:
    """``Table.Column`` for every column the plan reads, sorted."""
    tables = {t.alias: t.name for t in plan.tables}
    found: Set[str] = set()

    def walk(node: Optional[Expr]) -> None:
        if node is None:
            return
        if node.kind == "column" and node.column_name and node.column_name.strip() != "*":
            table = tables.get(node.alias or "") or (plan.tables[0].name if len(plan.tables) == 1 else None)
            if table:
                found.add(f"{table}.{node.column_name}")
        for child in (*node.args, node.left, node.right, node.expr, node.else_expr):
            walk(child)
        for when in node.whens:
            walk(when.condition)
            walk(when.result)

    for root in ([s.expr for s in plan.select_items] + [g.expr for g in plan.group_by]
                 + [o.expr for o in plan.order_by] + [j.condition for j in plan.joins]
                 + [plan.where, plan.having]):
        walk(root)
    return sorted(found)


def _sql(ctx: Any, datasource_id: str, proposed: SeedCase, user_context: Any) -> Optional[str]:
    """The engine's SQL for the proposed plan, validated under the caller's scope; None when it is refused."""
    from nl2sql.api.query_api import QueryAPI
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.pinned import SuppliedPlan

    try:
        # A question that reads as SQL is refused here, as the decomposer refuses an intent that does.
        sub_query = SubQuery(id="seed", datasource_id=datasource_id, intent=proposed.question)
    except ValidationError:
        return None
    result = QueryAPI(ctx).run_query(
        proposed.question, plan=SuppliedPlan(datasource_id=datasource_id, sub_query=sub_query, plan=proposed.plan),
        execute=False, user_context=user_context)
    sql = result.sub_queries[0].sql if result.sub_queries else ""
    return sql.strip() + "\n" if sql and sql.strip() else None


def _runs(ctx: Any, datasource_id: str, sql: str) -> str:
    """``ok``, ``failed`` or ``empty``: the SQL run through the datasource's adapter, its rows not kept."""
    frame = ctx.ds_registry.get_adapter(datasource_id).execute(
        AdapterRequest(plan_type="sql", payload={"sql": sql}))
    if not frame.success:
        return "failed"
    return "ok" if frame.row_count or frame.rows else "empty"


def seed(ctx: Any, datasource_id: str, *, count: int, out: pathlib.Path, user_context: Any,
         max_cost_usd: Optional[float], configured: Sequence[pathlib.Path] = ()) -> SeedReport:
    """Seed up to ``count`` draft cases for ``datasource_id`` into ``out``, as ``user_context`` sees it.

    See the module docstring. ``configured`` is the suites ``evals.yaml`` names,
    resolved, which drafts never go into.

    Raises:
        PermissionError: no caller, a caller with no role, or a role that cannot
            read the datasource or any of its tables.
        ValueError: ``count`` below 1, an unknown datasource, no positive
            ``max_cost_usd``, a refused ``out`` (see :func:`check_target`), or a
            datasource with no index build yet.
        UnknownPriceError: the ``eval_seeder`` model has no price.
    """
    from nl2sql.llm.prices import price_for
    from nl2sql.llm.wires import StructuredOutputError, structured
    from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_EXAMPLES
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
    from nl2sql.pipeline.nodes.schema_retriever.schema import render_schema_for_prompt
    from nl2sql.services.callbacks.token_handler import TokenUsageCallback

    out = pathlib.Path(out)
    if count < 1:
        raise ValueError("count must be at least 1.")
    if user_context is None or not list(user_context.roles or []):
        raise PermissionError("Seeding asks as a caller: pass a user_context with a role. The drafts hold what "
                              "that role may read, and a caller with no role reads nothing.")
    if datasource_id not in ctx.ds_registry.list_ids():
        raise ValueError(f"Unknown datasource '{datasource_id}'.")
    if not ctx.policy_engine.scope(user_context, datasource_id).reaches_datasource():
        raise PermissionError(f"The role(s) {', '.join(user_context.roles)} may not read the datasource "
                              f"'{datasource_id}'.")
    if not max_cost_usd or max_cost_usd <= 0:
        raise ValueError("Seeding spends money: it needs a positive max_cost_usd (dollars), in evals.yaml or "
                         "as an argument.")
    prices = dict(ctx.config.llm.prices)
    model = ctx.llm_registry.get_llm_config(SEED_AGENT).get("model")
    if price_for(model if isinstance(model, str) else None, prices) is None:
        raise UnknownPriceError(f"No price for {SEED_AGENT}: {model}. Add it to llm.prices (USD per 1M tokens: "
                                "input, output) before seeding.")
    check_target(out, configured)
    existing = load_suite(out).cases if out.is_file() else []
    header = file_header(out) if out.is_file() else HEADER

    tables = SchemaRetrieverNode(ctx).visible_tables(user_context, datasource_id)
    if not tables:
        if ctx.vector_store.snapshot(datasource_id) is None:
            raise ValueError(f"The datasource '{datasource_id}' has no index build yet: run `nl2sql index` first.")
        raise PermissionError(f"The role(s) {', '.join(user_context.roles)} may read no table of "
                              f"'{datasource_id}'.")

    budget = Budget(max_cost_usd=max_cost_usd, max_llm_calls=CALLS_PER_BATCH * math.ceil(count / BATCH_SIZE),
                    usage=TokenUsageCallback(prices=prices), setting=SETTING)
    chain = EVAL_SEEDER_PROMPT | structured(ctx.llm_registry.get_llm(SEED_AGENT), SeedBatch)
    fixed = {"examples": PLANNER_EXAMPLES, "tables": render_schema_for_prompt(tables),
             "today": run_date(ctx.settings.pipeline.today).isoformat()}
    report = SeedReport(path=out)
    seen = {_key(c.question) for c in existing}
    ids = {c.id for c in existing}
    outcome = {role: "allowed" for role in user_context.roles}
    drafts: List[EvalCase] = []
    try:
        while len(drafts) < count and budget.stop is None:
            asked = min(BATCH_SIZE, count - len(drafts))
            written = [c.question for c in existing + drafts]
            try:
                batch = chain.invoke({**fixed, "count": asked,
                                      "written": "\n".join(f"- {q}" for q in written) or "(none)"},
                                     config={"callbacks": budget.callbacks()})
            except LimitReached:
                break
            except StructuredOutputError as error:
                logger.warning(f"A seeding call gave no usable answer: {error}")
                report.dropped_invalid += asked
                continue
            for proposed in (batch.cases if batch is not None else [])[:asked]:
                key = _key(proposed.question)
                if key in seen:
                    report.dropped_duplicate += 1
                    continue
                seen.add(key)
                sql = _sql(ctx, datasource_id, proposed, user_context)
                if sql is None:
                    report.dropped_invalid += 1
                    continue
                ran = _runs(ctx, datasource_id, sql)
                if ran != "ok":
                    if ran == "failed":
                        report.dropped_failed += 1
                    else:
                        report.dropped_empty += 1
                    continue
                n = len(ids) + 1
                while f"{datasource_id}_seed_{n:03d}" in ids:
                    n += 1
                case_id = f"{datasource_id}_seed_{n:03d}"
                ids.add(case_id)
                drafts.append(EvalCase(
                    id=case_id, question=proposed.question.strip(), datasources=[datasource_id], status="draft",
                    origin="seeded", difficulty=proposed.difficulty, outcome=dict(outcome),
                    tables=sorted({t.name for t in proposed.plan.tables}), columns=_columns(proposed.plan),
                    sql=sql, plan=proposed.plan.model_dump(mode="json", exclude_none=True)))
    finally:
        if drafts:
            write_suite(out, {"version": 1, "cases": [c.model_dump(mode="json") for c in existing + drafts]},
                        header)
        report.written = len(drafts)
        report.stopped_at_cost_cap = budget.stop is not None and budget.stop.limit == "max_cost_usd"
        report.usage = budget.usage.usage()
    return report
