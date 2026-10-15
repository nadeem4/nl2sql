"""The MCP server: ``ask``, ``list_datasources`` and ``describe_table`` over the public facade.

``build_server(engine, identity)`` returns a ``FastMCP`` server from the
official ``mcp`` SDK; ``nl2sql mcp`` runs it over stdio. It imports the
top-level ``nl2sql`` and nothing else of the engine (the boundary test holds
it), so every tool is something any SDK caller can do. See
``docs/architecture/wave_a.md``, section 12, and ``docs/getting_started/mcp.md``.

Every tool runs as ``identity``: the tables and datasources the caller's scope
does not reach are hidden, and a denied table answers exactly as a missing one
does. Tools return structured results, never an exception's text: a failure
is a status and a code, and the details go to the log (stderr) and the trace.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Union

import anyio
from mcp.server.fastmcp import FastMCP

from nl2sql import NL2SQL, UserContext

logger = logging.getLogger(__name__)

# The rows of each sub-query an ``ask`` result carries; the full result stays
# in the engine's result store, read by trace id.
PREVIEW_ROWS = 20

# Who is asking: a fixed caller (stdio: whoever started the process), or a
# function asked on every call, so a server over HTTP can name one per request.
Identity = Union[UserContext, Callable[[], UserContext]]

INSTRUCTIONS = (
    "Answers questions about the connected databases in plain English. Call list_datasources to "
    "see what you can query and describe_table for a table's columns; ask runs a question and "
    "returns the answer, the SQL and the first rows. You see only what your role may read."
)

# The codes whose message the engine writes for the person asking: refusals,
# limits, provider failures and the answer checks' warnings. Any other error
# or warning (a crash, or an earlier attempt a retry recovered from) may carry
# an exception's text, so its message is replaced. ``ErrorCode`` values, named
# as text: the engine's own tests read ``ErrorCode.X`` as the place X is raised.
_CALLER_FACING = frozenset({
    "SECURITY_VIOLATION", "QUESTION_NOT_ANSWERABLE", "QUESTION_TOO_BROAD", "HOOK_DENIED", "NEEDS_APPROVAL",
    "RUN_LIMIT_REACHED", "CANCELLED", "PIPELINE_TIMEOUT", "PROVIDER_AUTH_FAILED", "PROVIDER_RATE_LIMITED",
    "PROVIDER_QUOTA_EXCEEDED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_MODEL_UNAVAILABLE",
    "VALUE_NOT_FOUND", "RESULT_TRUNCATED", "JOIN_KEY_UNCONVERTED", "JOIN_FAN_OUT", "ALL_NULL_COLUMN",
})


def build_server(engine: NL2SQL, identity: Identity) -> FastMCP:
    """An MCP server answering for ``identity`` from ``engine``. Run it with ``.run("stdio")``.

    ``identity`` is a ``UserContext``, or a function returning one that is
    called once per tool call. The engine stays the caller's to close.
    """
    caller = (lambda: identity) if isinstance(identity, UserContext) else identity
    server = FastMCP("nl2sql", instructions=INSTRUCTIONS)

    # The builtin ``dict`` return type makes the result the tool's structured
    # content as it is; ``typing.Dict`` would wrap it in ``{"result": ...}``.
    @server.tool()
    async def ask(question: str, datasource_id: Optional[str] = None) -> dict[str, Any]:
        """Answer a question about the data in plain English.

        Returns the run's status, the answer (summary and content), each
        sub-query's SQL with its columns and first 20 rows, whether rows were
        cut, the answer's source, warnings, and the trace id. A run that
        needs a person's approval has status needs_approval and lists
        approvals_needed; a run stopped at a limit carries stop. Pass
        datasource_id to skip routing.
        """
        return await _off_the_loop(_ask, engine, caller(), question, datasource_id)

    @server.tool()
    async def list_datasources() -> dict[str, Any]:
        """The datasources you may query: each one's id, type, description and status."""
        return await _off_the_loop(_list_datasources, engine, caller())

    @server.tool()
    async def describe_table(datasource_id: str, table: str) -> dict[str, Any]:
        """One table's columns (name, type, nullable, primary key, description), foreign keys, row count
        and description. ``table`` is its name, or schema.name. A table you may not read is not found."""
        return await _off_the_loop(_describe_table, engine, caller(), datasource_id, table)

    return server


async def _off_the_loop(tool: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    """Run a tool in a worker thread (the engine blocks); an exception is logged, never returned."""
    try:
        return await anyio.to_thread.run_sync(lambda: tool(*args))
    except Exception:
        logger.exception("The MCP tool %s failed", tool.__name__.lstrip("_"))
        return _error("INTERNAL_ERROR", "The server could not answer. Its log has the details.")


def _error(code: str, message: str, node: Optional[str] = None) -> dict[str, Any]:
    return {"status": "error", "errors": [{"code": code, "node": node, "message": message}]}


def _reachable(engine: NL2SQL, user: UserContext) -> list[Any]:
    """The configured datasources ``user``'s scope reaches, in config order."""
    return [info for info in engine.datasources.list()
            if engine.policies.scope(user, info.id).reaches_datasource()]


def _list_datasources(engine: NL2SQL, user: UserContext) -> dict[str, Any]:
    return {"datasources": [{"id": info.id, "type": info.type, "description": info.description,
                             "status": info.status} for info in _reachable(engine, user)]}


def _describe_table(engine: NL2SQL, user: UserContext, datasource_id: str, table: str) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    if datasource_id in {info.id for info in _reachable(engine, user)}:
        tables = engine.datasources.schema(datasource_id, user_context=user)["tables"]
    wanted = table.casefold()
    found = [t for t in tables if f"{t['schema']}.{t['name']}".casefold() == wanted] \
        or [t for t in tables if t["name"].casefold() == wanted]
    if len(found) == 1:
        return {"status": "ok", "datasource_id": datasource_id, "table": found[0]}
    names = [_qualified(t, tables) for t in tables]
    if found:
        return {"status": "ambiguous", "datasource_id": datasource_id, "tables": names,
                "message": f"More than one table is named '{table}'; name it as schema.table."}
    return {"status": "not_found", "datasource_id": datasource_id, "tables": names,
            "message": f"No table '{table}' in datasource '{datasource_id}'."}


def _qualified(table: dict[str, Any], tables: list[dict[str, Any]]) -> str:
    """The table's name, as schema.name when another schema has a table of that name."""
    twins = sum(1 for t in tables if t["name"] == table["name"])
    return f"{table['schema']}.{table['name']}" if twins > 1 else table["name"]


def _ask(engine: NL2SQL, user: UserContext, question: str, datasource_id: Optional[str]) -> dict[str, Any]:
    if datasource_id is not None and datasource_id not in {info.id for info in _reachable(engine, user)}:
        # A datasource the caller may not reach answers as a missing one does.
        return _error("DATASOURCE_NOT_FOUND", f"No datasource '{datasource_id}'.")
    result = engine.run_query(question, datasource_id=datasource_id, user_context=user)
    return _answer(result.model_dump(mode="json"))


def _answer(run: dict[str, Any]) -> dict[str, Any]:
    """``ask``'s result from a ``QueryResult`` as JSON: the answer, the SQL and the first rows.

    ``status`` is the run's: success, plan_only, error, needs_approval,
    stopped, or needs_clarification (Wave B's clarification adds its own key).
    """
    answer = run.get("final_answer") or None
    trace_id = run.get("trace_id") or ""
    sub_queries = [_sub_query(sq) for sq in run.get("sub_queries") or []]
    out: dict[str, Any] = {
        "status": run.get("status"),
        "answer": {"summary": answer.get("summary"), "content": answer.get("content")} if answer else None,
        "sub_queries": sub_queries,
        "truncated": bool(run.get("truncated")) or any(sq["truncated"] for sq in sub_queries),
        "source": run.get("source"),
        "warnings": [_problem(w, trace_id, node=False) for w in run.get("warnings") or []],
        "trace_id": trace_id,
    }
    if run.get("errors"):
        out["errors"] = [_problem(e, trace_id) for e in run["errors"]]
    if run.get("approvals_needed"):
        out["approvals_needed"] = run["approvals_needed"]
    if run.get("stop"):
        out["stop"] = run["stop"]
    return out


def _sub_query(sq: dict[str, Any]) -> dict[str, Any]:
    sample = sq.get("rows") or {}
    rows = list(sample.get("rows") or [])
    total = sample.get("total_rows") or len(rows)
    return {"id": sq.get("id"), "datasource_id": sq.get("datasource_id"), "sql": sq.get("sql"),
            "columns": list(sample.get("columns") or []), "rows": rows[:PREVIEW_ROWS], "total_rows": total,
            "truncated": bool(sq.get("truncated")) or total > min(len(rows), PREVIEW_ROWS)}


def _problem(problem: dict[str, Any], trace_id: str, node: bool = True) -> dict[str, Any]:
    """A run error (or, without ``node``, a warning) as its code and a message safe to show."""
    code = problem.get("error_code") or problem.get("code")
    if code in _CALLER_FACING:
        message = problem.get("message") or ""
    else:
        message = f"{code}: the trace {trace_id} has the details."
    return {"code": code, **({"node": problem.get("node") or None} if node else {}), "message": message}
