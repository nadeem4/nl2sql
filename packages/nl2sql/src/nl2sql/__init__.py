# nl2sql package

import importlib

from .public_api import NL2SQL, QueryResult

# Also expose individual API modules for more granular access
from .api.query_api import QueryAPI
from .api.datasource_api import DatasourceAPI
from .api.llm_api import LLM_API
from .api.indexing_api import IndexingAPI
from .api.auth_api import AuthAPI
from .api.settings_api import SettingsAPI
from .api.result_api import ResultAPI
from .api.policy_api import PolicyAPI

# Also expose core models and enums
from .common.errors import ErrorSeverity, ErrorCode, PipelineError
from .auth.models import UserContext

# The benchmark lives in nl2sql.evaluation, which the runtime never needs, so
# its names are imported only when first asked for.
_LAZY = {
    "BenchmarkAPI": "nl2sql.api.benchmark_api",
    "BenchmarkConfig": "nl2sql.evaluation.types",
}


def __getattr__(name):
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "NL2SQL",
    "QueryResult",
    "QueryAPI",
    "DatasourceAPI",
    "LLM_API",
    "IndexingAPI",
    "AuthAPI",
    "SettingsAPI",
    "ResultAPI",
    "PolicyAPI",
    "BenchmarkAPI",
    "ErrorSeverity",
    "ErrorCode",
    "PipelineError",
    "UserContext",
    "BenchmarkConfig",
]
