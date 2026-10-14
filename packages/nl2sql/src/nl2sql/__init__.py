# nl2sql package
#
# ``import nl2sql`` stays light: it loads the facade class and none of the
# runtime. Every other public name is imported the first time it is asked for,
# through the module's ``__getattr__``, so ``from nl2sql import QueryResult``
# works as before while ``import nl2sql`` loads no vector store, database
# driver or tracing library. The rule is in docs/architecture/invariants.md.

import importlib

from .public_api import NL2SQL

# The module that defines each public name. The shapes a client renders and
# the hooks an application entry point needs, so REST and SDK clients never
# import engine submodules.
_EXPORTS = {
    "nl2sql.api.query_api": ("QueryResult", "AnswerSource", "SubQueryResult", "RowSample"),
    "nl2sql.api.types": ("DatasourceInfo", "IndexProgress"),
    "nl2sql.llm.llm_set": ("LLMChange",),
    "nl2sql.execution.artifacts.results": ("MAX_RESULT_PAGE_SIZE", "ResultManifest", "ResultNotFound",
                                           "ResultPage", "ResultSummary"),
    "nl2sql.services.callbacks.token_handler": ("QuestionUsage",),
    "nl2sql.pipeline.events": ("QueryEvent",),
    "nl2sql.common.logger": ("configure_logging",),
    "nl2sql.common.errors": ("ErrorSeverity", "ErrorCode", "PipelineError"),
    "nl2sql.common.cancellation": ("CancellationToken",),
    "nl2sql.auth.models": ("UserContext",),
    "nl2sql.registry": ("Registry",),
    # The config, and what building an engine from it reports.
    "nl2sql.config": ("Config", "load_config", "load_env_file", "ConfigError", "Datasource", "Limits", "LLM", "AgentLLM",
                      "Embeddings", "Policies", "Role", "Storage", "StoreConfig", "Secrets", "Curated", "Settings"),
    "nl2sql.check": ("CheckReport", "DatasourceStatus", "EngineNotReady"),
    # The benchmark lives in nl2sql.evaluation, which the runtime never needs.
    "nl2sql.api.benchmark_api": ("BenchmarkAPI",),
    "nl2sql.evaluation.types": ("BenchmarkConfig",),
}
_LAZY = {name: module for module, names in _EXPORTS.items() for name in names}


def __getattr__(name):
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "NL2SQL",
    "QueryResult",
    "BenchmarkAPI",
    "ErrorSeverity",
    "ErrorCode",
    "PipelineError",
    "CancellationToken",
    "UserContext",
    "SubQueryResult",
    "RowSample",
    "AnswerSource",
    "ResultSummary",
    "ResultManifest",
    "ResultPage",
    "ResultNotFound",
    "MAX_RESULT_PAGE_SIZE",
    "QuestionUsage",
    "QueryEvent",
    "configure_logging",
    "BenchmarkConfig",
    "Registry",
    "Config",
    "load_config",
    "load_env_file",
    "ConfigError",
    "Datasource",
    "Limits",
    "LLM",
    "AgentLLM",
    "Embeddings",
    "Policies",
    "Role",
    "Storage",
    "StoreConfig",
    "Secrets",
    "Curated",
    "Settings",
    "CheckReport",
    "DatasourceStatus",
    "EngineNotReady",
    "DatasourceInfo",
    "IndexProgress",
    "LLMChange",
]
