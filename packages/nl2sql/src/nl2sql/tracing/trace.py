"""Assembles one run's trace document and writes it when ``TRACE_MODE`` says so.

Called by :func:`nl2sql.pipeline.runtime.run_with_graph` on every exit path, so
the CLI, the Python API, the REST API and the playground all get the same file.
Writing a trace never fails a run: any error here is logged and swallowed.
"""
from __future__ import annotations

import functools
import os
import pathlib
import platform
import re
import subprocess
from importlib import metadata
from typing import Any, Dict, Iterable, Optional, Set

from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.tracing.document import (
    TRACE_FORMAT_VERSION,
    Limits,
    Redactor,
    cap,
    jsonable,
    should_write,
    write_trace,
)

logger = get_logger("trace")

# Environment variables whose values are credentials, whatever the variable is.
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|CONNECTION_STRING|AUTH)", re.IGNORECASE)
_SECRET_SETTING = re.compile(r"(api_key|secret|password|token|connection_string|credential)", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def engine_info() -> Dict[str, Any]:
    """Package version, plus the git commit when running from a checkout."""
    try:
        version = metadata.version("nl2sql-engine")
    except metadata.PackageNotFoundError:
        version = "unknown"
    sha = None
    try:
        here = pathlib.Path(__file__).resolve().parent
        done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=here, capture_output=True,
                              text=True, timeout=2)
        if done.returncode == 0:
            sha = done.stdout.strip() or None
    except Exception:
        sha = None
    return {"version": version, "git_sha": sha, "python": platform.python_version()}


def collect_secrets(ctx: Any) -> Set[str]:
    """Every secret value this process knows of, for the redactor."""
    found: Set[str] = set()
    for name, value in os.environ.items():
        if value and _SECRET_ENV.search(name):
            found.add(value)
    for name in ("openai_api_key", "result_artifact_adls_connection_string"):
        value = getattr(settings, name, None)
        if value:
            found.add(str(value))
    for registry_name in ("llm_registry", "ds_registry"):
        registry = getattr(ctx, registry_name, None)
        manager = getattr(registry, "secret_manager", None)
        found.update(getattr(manager, "resolved_values", None) or ())
    for client in (getattr(getattr(ctx, "llm_registry", None), "llms", None) or {}).values():
        key = getattr(client, "openai_api_key", None)
        getter = getattr(key, "get_secret_value", None)
        if callable(getter) and getter():
            found.add(getter())
    return found


def settings_snapshot() -> Dict[str, Any]:
    """The settings in force, without any field that could hold a credential."""
    data = settings.model_dump(mode="json")
    return {k: v for k, v in data.items() if not _SECRET_SETTING.search(k)}


def _llm_configs(ctx: Any) -> Dict[str, Any]:
    configs = getattr(getattr(ctx, "llm_registry", None), "_configs", None) or {}
    return {
        name: {"provider": getattr(cfg, "provider", None), "model": getattr(cfg, "model", None),
               "temperature": getattr(cfg, "temperature", None)}
        for name, cfg in configs.items()
    }


def _failed(outcome: str, result: Dict[str, Any], nodes: Iterable[Dict[str, Any]]) -> bool:
    """A run needs debugging if it did not complete, reported errors, or had to retry."""
    if outcome != "completed" or result.get("status") == "error" or result.get("errors"):
        return True
    if any((sq or {}).get("retry_count") for sq in result.get("sub_queries") or []):
        return True
    return any(node.get("status") == "error" for node in nodes)


def build_trace(*, trace_id: str, request: Dict[str, Any], started_at: str, finished_at: str,
                duration_s: float, outcome: str, recorder, usage, ctx: Any, state: Dict[str, Any],
                error: Optional[str] = None) -> Dict[str, Any]:
    """The whole trace document, capped and redacted."""
    from nl2sql.api.query_api import result_from_state  # query_api imports the runtime

    limits = Limits(sample_rows=settings.trace_sample_rows, max_field_chars=settings.trace_max_field_chars)
    nodes = recorder.node_executions(usage=usage)
    for node in nodes:
        for key in ("inputs", "outputs", "errors", "warnings"):
            node[key] = cap(node[key], limits)
        for call in node["llm_calls"]:
            call["parsed"] = cap(call["parsed"], limits)

    try:
        result = result_from_state(state, artifact_store=getattr(ctx, "artifact_store", None),
                                   sample_rows=settings.trace_sample_rows).model_dump(mode="json")
        result.pop("trace_path", None)
    except Exception as exc:  # the trace is still worth having without the typed result
        result = {"error": f"Could not build the QueryResult: {exc}"}
    result = cap(result, limits)

    by_node: Dict[str, str] = {}
    for node in nodes:
        for call in node["llm_calls"]:
            by_node.setdefault(node["node"], call["model"])

    doc = {
        "trace_format_version": TRACE_FORMAT_VERSION,
        "trace_id": trace_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": duration_s,
        "outcome": outcome,
        "failed": _failed(outcome, result, nodes),
        "error": error,
        "request": request,
        "engine": engine_info(),
        "llm": {"configured": _llm_configs(ctx), "by_node": by_node},
        "settings": settings_snapshot(),
        "limits": {
            "sample_rows": limits.sample_rows,
            "max_field_chars": limits.max_field_chars,
            "max_list_items": limits.max_list_items,
            "note": ("Node inputs, outputs and the result are capped: rows to sample_rows, other lists "
                     "to max_list_items, strings to max_field_chars, each cut marked '[truncated]'. "
                     "LLM messages and raw responses are kept whole."),
        },
        "nodes": nodes,
        "result": result,
    }
    return Redactor(collect_secrets(ctx)).redact(jsonable(doc))


def write_run_trace(mode: str, **kwargs: Any) -> Optional[str]:
    """Builds the trace and writes it if ``mode`` asks for it; returns the path or None."""
    if mode == "off":
        return None
    try:
        doc = build_trace(**kwargs)
        if not should_write(mode, doc["failed"]):
            return None
        return str(write_trace(doc, pathlib.Path(settings.trace_dir)))
    except Exception:
        logger.warning("Could not write the run trace", exc_info=True)
        return None
