"""What ``nl2sql`` exports is a fixed list, so a change to the public surface is a deliberate one.

Adding or removing a name here is a semver decision: a removal is breaking.
See docs/architecture/configuration_v2.md, section 7.
"""
import nl2sql

PUBLIC_SURFACE = {
    # The engine and its questions
    "NL2SQL", "QueryResult", "SubQueryResult", "RowSample", "AnswerSource",
    "CancellationToken", "RunCancelled", "UserContext", "QuestionUsage", "RunStop", "ApprovalRequest", "Approval", "VerifiedRef", "SuppliedPlan",
    # Events
    "Event", "Observer",
    # Saved results
    "ResultSummary", "ResultManifest", "ResultPage", "ResultNotFound", "MAX_RESULT_PAGE_SIZE",
    # What the namespaces return
    "DatasourceInfo", "DatasourceType", "IndexProgress", "TileProgress", "LLMChange",
    # Schema drift
    "DriftReport", "DriftImpact", "SchemaDiff", "SchemaVersionInfo",
    # Errors
    "ErrorSeverity", "ErrorCode", "PipelineError", "RunWarning",
    # The config, and building an engine from it
    "Config", "load_config", "load_env_file", "ConfigError", "Registry", "Datasource", "Limits", "LLM",
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
    "NL2SQLError", "NotAuthorized", "InvalidArgument", "NotPinnable", "ChartNotSupported", "UnknownTile", "UnknownFilter", "ApprovalNotFound", "DashboardBusy", "MissingProviderKey", "DatasourceNotFound", "DatasourceExists",
    "LLMOverride", "resolve_mode", "provider_for_key", "provider_keys_in_environment",
}


def test_nl2sql_exports_exactly_the_public_surface():
    assert set(nl2sql.__all__) == PUBLIC_SURFACE
    assert len(nl2sql.__all__) == len(PUBLIC_SURFACE), "a name is listed twice in nl2sql.__all__"


def test_every_public_name_resolves():
    for name in PUBLIC_SURFACE:
        assert getattr(nl2sql, name) is not None, name
