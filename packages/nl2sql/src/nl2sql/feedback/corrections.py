"""Corrections: a thumbs-down with a fix becomes a checked ``verified_query`` proposal (wave_b.md, section 10; D55).

``engine.feedback.correct(trace_id, sql=... | plan=..., user_context=...)``:

1. **The run.** Its kept trace (or, without one, its rating) gives the
   question, unless ``question`` overrides it, the datasource it ran on and
   its SQL. Before anything else, the corrector's ``Scope`` must reach every
   datasource the run read and every table its SQL read (a run whose SQL
   cannot be read is refused), and the refusal names none of them: a
   corrector learns nothing of a run they could not have seen. Then a run on
   several datasources is refused, since a verified query answers from one,
   and the run is rated ``down`` with the note unless it already is.
2. **A plan fix** runs as a pinned plan (``run_query(plan=SuppliedPlan(...),
   execute=False)``) as the corrector: ``plan.after``, the logical validator
   and the generator, with no model call. A rejected plan is refused with the
   run's messages; the SQL is the engine's rendering.
3. **A SQL fix** must be one read-only query in the datasource's dialect
   (``inspect_verified_sql``, the curated config's own check; a trailing
   ``;`` and comment are dropped) with no placeholders. It reads plain tables
   only (no table-valued function such as a catalog or file reader) and calls
   only the functions a plan may call (``ALLOWED_FUNCTIONS``) and the
   expressions every query has (``CAST``, ``CASE``, ``EXISTS``, the current
   date). The corrector's ``Scope`` must allow every table it reads.
4. **The hooks.** The SQL passes the ``execute.before`` hooks with the
   corrector as the caller. A ``Deny``, a hook failure or a ``NeedsApproval``
   refuses it: a correction is never re-run with an approval.
5. **The check.** The SQL runs once through the adapter, as ``nl2sql curated
   test`` runs a verified query: read-only, under the datasource's limits, the
   first batch of rows only and none kept. A failure is refused with the
   adapter's message (an error it did not wrap is named by its type only);
   an empty result is allowed and recorded.
6. **The proposal**, filed with ``nl2sql.review.queue.propose``: origin
   ``correction``, ``change`` in ``curated.yaml``'s shape, ``tables`` every
   table the fix and the original SQL read (the evidence holds both), and
   ``evidence``: ``trace_id``, ``original_sql``, ``ran``, ``row_count``,
   ``columns``. Nothing is written to a person's file: only an accept does.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from nl2sql.common.logger import get_logger
from nl2sql.feedback.proposals import DESCRIPTION_PLACEHOLDER, _slug
from nl2sql.feedback.record import rated_row

logger = get_logger(__name__)

NODE = "feedback.correct"

# Why a correction was refused: CorrectionRefused.code.
REFUSAL_CODES = ("no_run", "multi_datasource", "invalid_plan", "not_read_only", "denied", "hook_denied",
                 "needs_approval", "failed")


class CorrectionRefused(ValueError):
    """A correction that failed one of its checks; nothing was proposed.

    ``code`` is one of :data:`REFUSAL_CODES`; ``messages`` are the checks' own
    words (the validator's, the hook's reason, the database's error).
    """

    def __init__(self, code: str, message: str, messages: Sequence[str] = ()):
        super().__init__(message)
        self.code = code
        self.messages = list(messages) or [message]


@dataclass(frozen=True)
class _Run:
    """What the corrected run was: its question, its one datasource, its SQL and how it was rated."""

    trace_id: str
    question: str
    datasource_id: str
    sql: Optional[str]
    result: Optional[Dict[str, Any]]   # the trace's QueryResult, when a trace was kept
    rating: Optional[Dict[str, Any]]   # the stored rating, when it was rated
    tables: List[Tuple[str, str]] = field(default_factory=list)  # what the run's SQL read


DENIED_RUN = "This role may not read what the run read, so it cannot correct it."


def _run_tables(ctx: Any, datasource_id: str, sql: str) -> List[Tuple[str, str]]:
    """The tables the run's own SQL read; refused (naming nothing) when it cannot be read."""
    from nl2sql.config.curated import CuratedSqlError, inspect_verified_sql

    try:
        return inspect_verified_sql(sql, ctx.ds_registry.get_dialect(datasource_id)).tables
    except CuratedSqlError:
        raise CorrectionRefused("denied", "The run's SQL cannot be read, so it cannot be checked against this "
                                          "role.") from None


def _find_run(ctx: Any, trace_id: str, user: Any) -> _Run:
    """The run, after the corrector's scope is checked over everything it read (see the module docstring)."""
    from nl2sql.tracing.document import validate_trace_id

    rating = next((row for row in ctx.state.list_feedback() if row.get("trace_id") == trace_id), None)
    try:
        trace = ctx.traces.get(validate_trace_id(trace_id))
    except (ValueError, OSError):
        trace = None
    if trace is not None:
        result = trace.get("result") or {}
        question = (trace.get("request") or {}).get("question") or ""
        sub_queries = [s for s in result.get("sub_queries") or [] if isinstance(s, dict)]
        datasource_ids = list(dict.fromkeys(str(s["datasource_id"]) for s in sub_queries if s.get("datasource_id")))
        sql = [s["sql"] for s in sub_queries if s.get("sql")]
    elif rating is not None:
        result, question = None, rating.get("question") or ""
        datasource_ids, sql = list(rating.get("datasource_ids") or []), [s for s in rating.get("sql") or [] if s]
    else:
        raise CorrectionRefused("no_run", f"No run '{trace_id}' was kept: neither its trace nor a rating of it. "
                                          "Keep traces (settings.traces.mode: always) to correct an answer.")
    if not datasource_ids:
        raise CorrectionRefused("no_run", f"The run '{trace_id}' ran on no datasource, so there is nothing to "
                                          "correct.")
    # The scope first: nothing about a run reaches a caller who could not have read what it read.
    configured = set(ctx.ds_registry.list_ids())
    for datasource_id in datasource_ids:
        if datasource_id not in configured or not ctx.policy_engine.scope(user, datasource_id).reaches_datasource():
            raise CorrectionRefused("denied", DENIED_RUN)
    if len(datasource_ids) > 1:
        raise CorrectionRefused("multi_datasource", f"The run '{trace_id}' read {', '.join(datasource_ids)}; a "
                                                    "verified query answers from one datasource.")
    datasource_id = datasource_ids[0]
    tables = sorted({pair for statement in sql for pair in _run_tables(ctx, datasource_id, statement)})
    scope = ctx.policy_engine.scope(user, datasource_id)
    if not all(scope.allows(schema or None, table) for schema, table in tables):
        raise CorrectionRefused("denied", DENIED_RUN)
    return _Run(trace_id=trace_id, question=" ".join(question.split()), datasource_id=datasource_id,
                sql=sql[0] if sql else None, result=result, rating=rating, tables=tables)


def _rate_down(ctx: Any, run: _Run, user: Any, note: Optional[str], rate: Callable[..., Any]) -> None:
    """Rate the run ``down`` with ``note``, unless it already is."""
    if run.rating is not None and run.rating.get("rating") == "down":
        return
    if run.result is not None:
        rate({**run.result, "trace_id": run.trace_id}, question=run.question, role=",".join(user.roles),
             rating="down", note=note)
    else:
        ctx.state.add_feedback(rated_row(run.rating, "down", note))


def _plan_model(plan: Any):
    from pydantic import ValidationError

    from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

    try:
        if isinstance(plan, PlanModel):
            return plan
        if isinstance(plan, (str, bytes)):
            return PlanModel.model_validate(json.loads(plan))
        return PlanModel.model_validate(plan)
    except (ValidationError, ValueError) as error:
        raise CorrectionRefused("invalid_plan", f"The plan is not a valid plan: {error}") from None


def _sql_of_plan(ctx: Any, run: _Run, plan: Any, question: str, user: Any) -> Tuple[str, Any]:
    """The plan validated and generated as ``user`` would see it run: ``(sql, plan as rendered)``."""
    from pydantic import ValidationError

    from nl2sql.api.query_api import QueryAPI
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.pinned import SuppliedPlan

    model = _plan_model(plan)
    try:
        sub_query = SubQuery(id="correction", datasource_id=run.datasource_id, intent=question)
    except ValidationError as error:
        raise CorrectionRefused("invalid_plan", f"The question cannot be asked: {error}") from None
    result = QueryAPI(ctx).run_query(
        question, plan=SuppliedPlan(datasource_id=run.datasource_id, sub_query=sub_query, plan=model),
        execute=False, user_context=user)
    done = result.sub_queries[0] if result.sub_queries else None
    errors = [e for e in result.errors if isinstance(e, dict)]
    approval = next((e for e in errors if str(e.get("error_code")) in ("NEEDS_APPROVAL", "ErrorCode.NEEDS_APPROVAL")),
                    None)
    if approval is not None or result.status == "needs_approval":
        reason = str((approval or {}).get("message") or "A hook asks for approval.")
        raise CorrectionRefused("needs_approval", f"{reason} A correction is not run with an approval.", [reason])
    if done is None or not (done.sql or "").strip() or errors:
        messages = [str(e.get("message")) for e in errors if e.get("message")]
        messages += [c.message for c in (done.validation if done else []) if not c.passed and c.message]
        messages = list(dict.fromkeys(messages)) or ["The plan produced no SQL."]
        raise CorrectionRefused("invalid_plan", "The plan was refused: " + " ".join(messages), messages)
    return done.sql.strip(), type(model).model_validate(done.plan) if done.plan else model


# What every query may hold besides the functions a plan may call: casts, CASE, EXISTS and the current
# date and time. Anything else a SQL fix calls is refused.
STRUCTURAL_FUNCTIONS = frozenset({"CAST", "TRY_CAST", "CASE", "IF", "EXISTS", "CURRENT_DATE", "CURRENT_TIME",
                                  "CURRENT_TIMESTAMP"})


@functools.lru_cache(maxsize=None)
def _known_functions(dialect: str) -> FrozenSet[str]:
    """The names sqlglot gives, in ``dialect``, to the functions a plan may call (``ALLOWED_FUNCTIONS``).

    sqlglot reads some under another name (``STRFTIME`` as ``TIME_TO_STR``)
    or wraps an argument (``YEAR(d)``), so each is parsed with one and with two
    arguments and every function name it yields is kept.
    """
    import sqlglot
    from sqlglot import exp

    from nl2sql.pipeline.nodes.ast_planner.functions import ALLOWED_FUNCTIONS

    names = set(ALLOWED_FUNCTIONS) | STRUCTURAL_FUNCTIONS
    for name in ALLOWED_FUNCTIONS:
        for args in ("x", "'day', x"):
            try:
                tree = sqlglot.parse_one(f"SELECT {name}({args})", read=dialect)
            except Exception:  # noqa: BLE001 -- that arity does not parse; the other may
                continue
            names.update(n.sql_name() for n in tree.find_all(exp.Func) if not isinstance(n, exp.Anonymous))
    return frozenset(names)


def _one_statement(sql: str, dialect: str) -> str:
    """``sql`` without a trailing ``;`` (and the comment after it): that is not a second statement."""
    import sqlglot
    from sqlglot.tokens import TokenType

    try:
        tokens = sqlglot.Dialect.get_or_raise(dialect).tokenize(sql)
    except Exception:  # noqa: BLE001 -- the check that follows says what is wrong with it
        return sql.strip()
    for i, token in enumerate(tokens):
        if token.token_type == TokenType.SEMICOLON and all(t.token_type == TokenType.SEMICOLON
                                                            for t in tokens[i + 1:]):
            return sql[:token.start].strip()
    return sql.strip()


def _plain(sql: str, dialect: str) -> None:
    """Refuse a table-valued function as a source, and a call to anything a plan may not call."""
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, read=dialect)
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise CorrectionRefused("not_read_only", f"The SQL fix may read only tables, not {table.this.sql()}.")
    known = _known_functions(dialect)
    for node in tree.find_all(exp.Func):
        if isinstance(node, exp.Connector):  # AND, OR: operators sqlglot models as functions
            continue
        name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
        if name not in known:
            raise CorrectionRefused("not_read_only", f"The SQL fix may call only the functions a plan may call, "
                                                     f"not {name}.")


def _read_tables(ctx: Any, datasource_id: str, sql: str, *, written: bool = False) -> List[Tuple[str, str]]:
    """The tables ``sql`` reads, after checking it is one read-only query with no placeholders.

    ``written`` is a person's SQL fix, which is also held to plain tables and
    the functions a plan may call (``_plain``); the engine's own rendering of a
    plan already is.
    """
    from nl2sql.config.curated import CuratedSqlError, inspect_verified_sql

    dialect = ctx.ds_registry.get_dialect(datasource_id)
    try:
        parsed = inspect_verified_sql(sql, dialect)
    except CuratedSqlError as error:
        raise CorrectionRefused("not_read_only", f"The SQL fix must be one read-only query: {error}.") from None
    if parsed.placeholders:
        raise CorrectionRefused("not_read_only", "The SQL fix runs once, so it holds the values, not :name "
                                                 "placeholders; the reviewer turns values into params on accept.")
    if written:
        _plain(sql, dialect)
    return parsed.tables


def _check_scope(ctx: Any, datasource_id: str, tables: Sequence[Tuple[str, str]], user: Any) -> None:
    """Refuse a table the corrector's ``Scope`` does not allow."""
    scope = ctx.policy_engine.scope(user, datasource_id)
    if not scope.reaches_datasource():
        raise CorrectionRefused("denied", f"The datasource '{datasource_id}' is not allowed for this role.")
    denied = [f"{datasource_id}.{schema + '.' if schema else ''}{table}" for schema, table in tables
              if not scope.allows(schema or None, table)]
    if denied:
        raise CorrectionRefused("denied", f"Not allowed for this role: {', '.join(denied)}.")


def _hooked(ctx: Any, run: _Run, question: str, sql: str, plan: Any, tables: List[str], user: Any) -> None:
    """Refuse the SQL when an ``execute.before`` hook denies it, fails, or asks for approval."""
    from nl2sql.common.errors import ErrorCode
    from nl2sql.hooks import hooks_of
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

    sub_query = SubQuery.model_construct(id="correction", datasource_id=run.datasource_id, intent=question)
    # What a question's executor shows the hooks (ExecutorNode._hook_fields); no approvals: a correction is
    # never re-run with one.
    verdict = hooks_of(ctx).call("execute.before", trace_id=run.trace_id, question=question, user=user,
                                 datasource_id=run.datasource_id, sub_query=sub_query, plan=plan, sql=sql,
                                 params={}, tables=tables, approvals=[])
    if verdict.allowed:
        return
    error = verdict.error(NODE)
    if error.error_code == ErrorCode.NEEDS_APPROVAL:
        raise CorrectionRefused("needs_approval", f"The hook '{verdict.hook}' asks for approval "
                                                  f"({verdict.decision.reason}); a correction is not run with "
                                                  "an approval.", [verdict.decision.reason])
    raise CorrectionRefused("hook_denied", error.message, [error.message])


def _ran(ctx: Any, datasource_id: str, sql: str) -> Dict[str, Any]:
    """Run ``sql`` once, read-only, the first batch only: ``{ran, row_count, columns}``; refused on failure."""
    from nl2sql_sdk.contracts import AdapterRequest

    from nl2sql.execution.executor.sql_executor import _frames

    request = AdapterRequest(plan_type="sql", payload={"sql": sql}, parameters={}, limits={})
    frames = _frames(ctx.ds_registry.get_adapter(datasource_id), request, ctx.settings.results.fetch_batch_size)
    try:
        frame = next(frames, None)
    except Exception as error:  # a driver error the adapter did not wrap: its text may hold a host or a password
        logger.warning("A correction's check run failed on %s: %s", datasource_id, error)
        reason = f"the database driver raised {type(error).__name__}"
        raise CorrectionRefused("failed", f"The SQL failed: {reason}.", [reason]) from None
    finally:
        close = getattr(frames, "close", None)
        if close is not None:
            close()
    if frame is None or not frame.success:
        reason = frame.error.safe_message if frame is not None and frame.error else "no result"
        raise CorrectionRefused("failed", f"The SQL failed: {reason}", [reason])
    return {"ran": True, "row_count": frame.row_count, "columns": list(frame.columns or [])}


def correct(ctx: Any, trace_id: str, *, sql: Optional[str], plan: Any, question: Optional[str],
            note: Optional[str], user_context: Any, rate: Callable[..., Any]):
    """Check the fix of run ``trace_id`` as ``user_context`` and file it as a proposal; see the module docstring.

    ``rate`` is ``engine.feedback.record``, which rates the run ``down``.

    Returns:
        The ``Proposal`` as the queue stored it.

    Raises:
        TypeError: ``user_context`` is not a ``UserContext``.
        ValueError: not exactly one of ``sql`` and ``plan``.
        CorrectionRefused: a check failed (its ``code`` says which).
    """
    from nl2sql_sdk.stores import Proposal

    from nl2sql.review.queue import caller, propose, table_names

    user = caller(user_context)
    if (sql is None) == (plan is None):
        raise ValueError("Give exactly one fix: sql or plan.")
    run = _find_run(ctx, trace_id, user)
    _rate_down(ctx, run, user, note, rate)
    asked = " ".join((question or run.question).split())
    if not asked:
        raise CorrectionRefused("no_run", "The run has no question; pass question=.")

    if plan is not None:
        fixed, plan = _sql_of_plan(ctx, run, plan, asked, user)
    else:
        fixed, plan = _one_statement(sql, ctx.ds_registry.get_dialect(run.datasource_id)), None
    pairs = _read_tables(ctx, run.datasource_id, fixed, written=plan is None)
    _check_scope(ctx, run.datasource_id, pairs, user)
    names = table_names(ctx, run.datasource_id, pairs)
    _hooked(ctx, run, asked, fixed, plan, names, user)
    evidence = {"trace_id": run.trace_id, "original_sql": run.sql, **_ran(ctx, run.datasource_id, fixed)}

    change = {"id": _slug(asked), "description": (note or "").strip() or DESCRIPTION_PLACEHOLDER,
              "questions": [asked], "sql": fixed + "\n"}
    sub_queries = (run.result or {}).get("sub_queries") or [{}]
    return propose(ctx, Proposal.build(
        kind="verified_query", datasource_id=run.datasource_id, target=asked, change=change, origin="correction",
        tables=table_names(ctx, run.datasource_id, [*pairs, *run.tables]), evidence=evidence,
        schema_version=(sub_queries[0] or {}).get("schema_version") or None, created_by=user.user_id))
