# nl2sql package

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
from .api.benchmark_api import BenchmarkAPI

# Also expose core models and enums
from .common.errors import ErrorSeverity, ErrorCode, PipelineError
from .auth.models import UserContext
from .evaluation.types import BenchmarkConfig

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
