"""What one run contributes to the feedback table and the guardrail rates.

``run_signals`` reads the counters the stats report from a ``QueryResult``
(as JSON), and from the run's trace when there is one. ``run_record`` adds the
question, role, status, SQL, the model per node and the engine version.

Privacy: a record never holds result rows, the written answer, sample values,
error messages or keys; only error *codes*. It holds only what the run showed
the role it ran as, and a run refused for that role (``SECURITY_VIOLATION``)
keeps no SQL.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from nl2sql.llm.providers import LLM_AGENTS

# The error codes that mean the engine refused the question, rather than failed.
REFUSAL_CODES = ("SECURITY_VIOLATION", "QUESTION_NOT_ANSWERABLE")


def _codes(result: Mapping[str, Any]) -> List[str]:
    """Each blocking error code once, in the order it first appeared."""
    seen: List[str] = []
    for error in result.get("errors") or []:
        code = (error or {}).get("error_code") if isinstance(error, dict) else None
        if code and code not in seen:
            seen.append(str(code))
    return seen


def run_signals(result: Mapping[str, Any], trace: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The counters for one run.

    Validator failures come from the trace when there is one (every logical
    validator rejection, including ones a retry then fixed); without a trace,
    from the checks the final result reports as failed.
    """
    sub_queries = [s for s in (result.get("sub_queries") or []) if isinstance(s, dict)]
    if trace is not None:
        failures = sum(1 for n in trace.get("nodes") or []
                       if n.get("node") == "logical_validator" and (n.get("status") == "error" or n.get("errors")))
    else:
        failures = sum(1 for s in sub_queries
                       if any(not (c or {}).get("passed", True) for c in s.get("validation") or []))
    return {
        "error_codes": _codes(result),
        "retries": sum(int(s.get("retry_count") or 0) for s in sub_queries),
        "validator_failures": failures,
        "plan_cache_hits": sum(1 for s in sub_queries if s.get("plan_source") == "cache"),
        "sub_queries": len(sub_queries),
    }


def _agent(node: str) -> str:
    """The agent name the LLM config knows ``node`` under (ast_planner -> astplanner)."""
    return LLM_AGENTS.get(node, node)


def models_by_node(result: Mapping[str, Any], llm_configs: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The provider and model each LLM node ran on: the model it reported, the provider configured for it."""
    default = llm_configs.get("default") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for call in ((result.get("usage") or {}).get("calls") or []):
        node = call.get("node")
        if not node or node in out:
            continue
        config = llm_configs.get(_agent(node)) or default
        out[node] = {"provider": config.get("provider"), "model": call.get("model") or config.get("model")}
    return out


def run_record(result: Mapping[str, Any], *, question: str, role: str,
               llm_configs: Mapping[str, Mapping[str, Any]], engine_version: str,
               trace: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Everything the feedback table keeps about one run, before it is rated."""
    signals = run_signals(result, trace)
    refused = "SECURITY_VIOLATION" in signals["error_codes"]
    sql = [] if refused else [s.get("sql") for s in result.get("sub_queries") or []
                              if isinstance(s, dict) and s.get("sql")]
    return {
        "trace_id": str(result.get("trace_id") or ""),
        "question": question,
        "role": role,
        "status": str(result.get("status") or ""),
        "sql": sql,
        "models": models_by_node(result, llm_configs),
        "engine_version": engine_version,
        **signals,
    }
