"""What ``nl2sql`` exports is a fixed list, so a change to the public surface is a deliberate one.

Adding or removing a name here is a semver decision: a removal is breaking.
See docs/architecture/configuration_v2.md, section 7.
"""
import nl2sql

PUBLIC_SURFACE = {
    # The engine and its questions
    "NL2SQL", "QueryResult", "SubQueryResult", "RowSample", "AnswerSource",
    "CancellationToken", "RunCancelled", "UserContext", "QuestionUsage", "RunStop", "ApprovalRequest", "Approval", "ApprovalRecord", "VerifiedRef", "SuppliedPlan",
    # Events
    "Event", "Observer", "EventInfo",
    # Saved results
    "ResultSummary", "ResultManifest", "ResultPage", "ResultNotFound", "TraceNotFound", "MAX_RESULT_PAGE_SIZE",
    # What the namespaces return
    "DatasourceInfo", "DatasourceType", "IndexProgress", "TileProgress", "LLMChange",
    # Schema drift
    "DriftReport", "DriftImpact", "SchemaDiff", "SchemaVersionInfo",
    # Errors
    "ErrorSeverity", "ErrorCode", "PipelineError", "RunWarning",
    # The config, and building an engine from it
    "Config", "load_config", "prepare_demo", "init_project", "load_env_file", "ConfigError", "Registry", "Datasource", "Limits", "LLM",
    "AgentLLM", "Embeddings", "Policies", "Role", "Storage", "StoreConfig", "Secrets", "Curated", "Settings",
    "ObserverConfig", "HookConfig", "EvalsConfig",
    "CheckReport", "DatasourceStatus", "EngineNotReady",
    # Entry-point hooks
    "configure_logging", "TraceContextFilter",
    # Evals: suites, reports and the scorer interface
    "EvalSuite", "EvalCase", "EvalReport", "RegenerateReport", "GateResult", "Scorer", "Score", "CaseRun",
    # Wave B: knowledge, the review queue, the profiler, clarification and the explanation
    "KnowledgeSourceConfig", "CostRule", "KnowledgeSourceInfo", "DatasourceKnowledgeView", "Proposal",
    "AcceptReport", "ReviewError", "ProfileReport", "ClarificationRequest", "ClarificationOption",
    "ClarificationChoice", "Explanation", "ExplainedStep", "TermUse",
    # Wave C: conversations, charts, dashboards and workspaces, chat tools, the launchpad and API keys
    "Conversation", "Turn", "ToolStep", "ToolCall", "ToolOutput", "ModelStep", "ConversationInfo",
    "ChartSpec", "ChartData", "Encoding", "ColumnKind", "diagram_for",
    "Workspace", "Dashboard", "DashboardInfo", "DashboardView", "DashboardRun", "Tile", "TileRun", "TileLayout",
    "ChartPlan", "StoredQuery", "DashboardFilter", "Schedule", "ScheduleIdentity", "SkippedTile", "StepRef",
    "TileArrangement", "DashboardEdit", "RenameDashboard", "RenameTile", "SetTileChart", "RemoveTile", "Arrange",
    "SetFilters", "SetSchedule", "MoveDashboard", "AddTile", "ReplacePlan",
    "ChatTool", "ToolContext", "ToolNotApplicable", "ChatToolInfo", "Candidate", "Found", "DiscoverableAdapter",
    "ApiKey",
    "ConversationNotFound", "ConversationBusy", "DashboardNotFound", "WorkspaceNotFound", "NotIndexed", "NoTiles",
    "WorkspaceNotEmpty", "ScheduleTooFrequent", "SecretValueInConfig",
    "ConnectionInvalid",
    "NL2SQLError", "NotAuthorized", "InvalidArgument", "ReplayMiss", "NotPinnable", "ChartNotSupported", "CorrectionRefused", "UnknownTile", "UnknownFilter", "ApprovalNotFound", "DashboardBusy", "MissingProviderKey", "DatasourceNotFound", "DatasourceExists",
    "LLMOverride", "resolve_mode", "provider_for_key", "provider_keys_in_environment",
    # The CLI is a client of the facade (CL0a): the config check and the provider key facts it prints
    "check_config", "KEYED_PROVIDERS", "api_key_from_env", "env_var_for_provider", "ANTHROPIC_EXTRA_HINT",
    # CL0b: what the rest of the CLI commands read from the engine
    "looks_like_api_key", "KEY_SHAPE_MESSAGE", "LLMFileConfig",
    "check_file_name", "load_trace", "load_llm_file", "resolve_llm_spec", "import_records", "publish_records", "run_stamp", "write_records", "RegistryError",
    "default_llm", "starter_config", "write_env_values", "write_project",
}


def test_nl2sql_exports_exactly_the_public_surface():
    assert set(nl2sql.__all__) == PUBLIC_SURFACE
    assert len(nl2sql.__all__) == len(PUBLIC_SURFACE), "a name is listed twice in nl2sql.__all__"


def test_every_public_name_resolves():
    for name in PUBLIC_SURFACE:
        assert getattr(nl2sql, name) is not None, name


def test_the_facade_gives_the_cli_its_config_check_and_provider_facts(monkeypatch):
    from nl2sql.config.check import check_config as engine_check_config

    assert nl2sql.check_config is engine_check_config
    assert nl2sql.env_var_for_provider("openai") == "OPENAI_API_KEY"
    assert "openai" in nl2sql.KEYED_PROVIDERS and "ollama" not in nl2sql.KEYED_PROVIDERS
    monkeypatch.setenv("OPENAI_API_KEY", "x" * 24)
    assert nl2sql.api_key_from_env("openai") == "x" * 24
    assert "nl2sql-engine[anthropic]" in nl2sql.ANTHROPIC_EXTRA_HINT
