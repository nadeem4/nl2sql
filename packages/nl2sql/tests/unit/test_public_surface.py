"""What ``nl2sql`` exports is a fixed list, so a change to the public surface is a deliberate one.

Adding or removing a name here is a semver decision: a removal is breaking.
See docs/architecture/configuration_v2.md, section 7.
"""
import nl2sql

PUBLIC_SURFACE = {
    # The engine and its questions
    "NL2SQL", "QueryResult", "SubQueryResult", "RowSample", "AnswerSource",
    "CancellationToken", "UserContext", "QuestionUsage", "RunStop", "ApprovalRequest", "Approval", "VerifiedRef",
    # Events
    "Event", "Observer",
    # Saved results
    "ResultSummary", "ResultManifest", "ResultPage", "ResultNotFound", "MAX_RESULT_PAGE_SIZE",
    # What the namespaces return
    "DatasourceInfo", "DatasourceType", "IndexProgress", "LLMChange",
    # Errors
    "ErrorSeverity", "ErrorCode", "PipelineError",
    # The config, and building an engine from it
    "Config", "load_config", "load_env_file", "ConfigError", "Registry", "Datasource", "Limits", "LLM",
    "AgentLLM", "Embeddings", "Policies", "Role", "Storage", "StoreConfig", "Secrets", "Curated", "Settings",
    "ObserverConfig", "HookConfig", "EvalsConfig",
    "CheckReport", "DatasourceStatus", "EngineNotReady",
    # Entry-point hooks
    "configure_logging", "TraceContextFilter",
    # Evals: suites, reports and the scorer interface
    "EvalSuite", "EvalCase", "EvalReport", "RegenerateReport", "Scorer", "Score", "CaseRun",
}


def test_nl2sql_exports_exactly_the_public_surface():
    assert set(nl2sql.__all__) == PUBLIC_SURFACE
    assert len(nl2sql.__all__) == len(PUBLIC_SURFACE), "a name is listed twice in nl2sql.__all__"


def test_every_public_name_resolves():
    for name in PUBLIC_SURFACE:
        assert getattr(nl2sql, name) is not None, name
