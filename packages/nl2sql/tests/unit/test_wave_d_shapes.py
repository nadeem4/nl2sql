"""Wave D, D0a-1: the engine shapes the API and the web app build on (docs/architecture/wave_d.md, section 14)."""
from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import nl2sql
from nl2sql.common import errors
from nl2sql.config import Api, Config, ConfigError, load_config
from nl2sql.config.mode import resolve_mode
from nl2sql.llm.override import LLMOverride
from nl2sql.storage.sqlite import MIGRATIONS, SqliteState

MINIMAL = """\
version: 2
datasources: []
llm:        { provider: anthropic, model: claude-sonnet-5-5 }
embeddings: { provider: local }
policies:   { allow_all: true }
storage:    { path: .nl2sql }
"""


def _load(tmp_path: Path, extra: str = "") -> Config:
    path = tmp_path / "nl2sql.yaml"
    path.write_text(MINIMAL + textwrap.dedent(extra), encoding="utf-8")
    return load_config(path)


# ---------------------------------------------------------------- errors

def _named_exceptions():
    from nl2sql.dashboards.run import DashboardBusy
    from nl2sql.execution.artifacts.results import ResultNotFound
    from nl2sql.llm.request_key import MissingProviderKey
    from nl2sql.review.models import ReviewError

    return [
        (errors.ConversationNotFound, LookupError, 404, "NOT_FOUND"),
        (errors.DashboardNotFound, LookupError, 404, "NOT_FOUND"),
        (errors.WorkspaceNotFound, LookupError, 404, "NOT_FOUND"),
        (ResultNotFound, LookupError, 404, "NOT_FOUND"),
        (errors.ApprovalNotFound, LookupError, 404, "NOT_FOUND"),
        (errors.ConversationBusy, LookupError, 409, "CONFLICT"),
        (DashboardBusy, ValueError, 409, "CONFLICT"),
        (errors.NoTiles, ValueError, 422, "NO_TILES"),
        (errors.WorkspaceNotEmpty, ValueError, 422, "WORKSPACE_NOT_EMPTY"),
        (errors.ScheduleTooFrequent, ValueError, 422, "SCHEDULE_TOO_FREQUENT"),
        (errors.NotIndexed, ValueError, 422, "NOT_INDEXED"),
        (errors.SecretValueInConfig, ValueError, 422, "SECRET_VALUE_IN_CONFIG"),
        (errors.ConnectionInvalid, ValueError, 422, "CONNECTION_INVALID"),
        (MissingProviderKey, Exception, 422, "MISSING_PROVIDER_KEY"),
        (errors.NotAuthorized, PermissionError, 403, "NOT_AUTHORIZED"),
        (errors.DatasourceNotFound, ValueError, 404, "NOT_FOUND"),
        (errors.DatasourceExists, ValueError, 409, "CONFLICT"),
        (ReviewError, Exception, None, None),
    ]


def test_named_exceptions_keep_their_builtin_parents():
    for cls, builtin, status, code in _named_exceptions():
        assert issubclass(cls, nl2sql.NL2SQLError), cls
        assert issubclass(cls, builtin), cls
        if status is not None:
            assert (cls.status, cls.code) == (status, code), cls
    # The pre-existing base is the same class, not a second one.
    from nl2sql.common.exceptions import NL2SQLError, PipelineExecutionError
    assert NL2SQLError is nl2sql.NL2SQLError
    assert issubclass(PipelineExecutionError, nl2sql.NL2SQLError)


def test_nl2sql_error_carries_message_fix_and_status():
    error = errors.NoTiles("Nothing to chart.", fix="Index a datasource first.")
    assert (error.message, error.fix, error.code, error.status) == (
        "Nothing to chart.", "Index a datasource first.", "NO_TILES", 422)
    assert str(error) == "Nothing to chart."
    with pytest.raises(ValueError):
        raise error


def test_connection_invalid_and_review_error_keep_their_shapes():
    from nl2sql.review.models import ReviewError, ReviewErrorCode

    invalid = errors.ConnectionInvalid([{"path": "connection.port", "message": "must be a number"}])
    assert isinstance(invalid, nl2sql.NL2SQLError) and invalid.status == 422
    assert invalid.problems[0]["path"] == "connection.port"

    statuses = {ReviewErrorCode.NOT_FOUND: 404, ReviewErrorCode.FILE_CHANGED: 409,
                ReviewErrorCode.NOT_AUTHORIZED: 403, ReviewErrorCode.NOT_WRITABLE: 403}
    for code, status in statuses.items():
        error = ReviewError(code, "No.", fix="Do this.")
        assert (error.status, error.fix) == (status, "Do this.")
        assert error.code == code


def test_new_error_codes_are_not_retried():
    for code in (errors.ErrorCode.REPLAY_MISS, errors.ErrorCode.APPROVAL_NOT_GRANTED):
        assert code in errors.FATAL_ERRORS


def test_unknown_and_taken_datasource_ids_raise_named_errors(tmp_path):
    config = _load(tmp_path)
    shop = nl2sql.Datasource(id="shop", type="postgres")
    added = config.with_datasource(shop)
    with pytest.raises(nl2sql.DatasourceExists) as taken:
        added.with_datasource(shop)
    assert taken.value.status == 409 and isinstance(taken.value, ValueError)
    with pytest.raises(nl2sql.DatasourceNotFound) as unknown:
        config.without_datasource("nope")
    assert unknown.value.status == 404 and isinstance(unknown.value, ValueError)


def test_trusted_from_must_be_networks():
    assert Api(proxy={"trusted_from": ["10.0.0.0/8", "::1", "192.168.1.5"]}).proxy.trusted_from
    for bad in ("not-a-cidr", "10.0.0.0/33", ""):
        with pytest.raises(ValidationError, match="trusted_from"):
            Api(proxy={"trusted_from": [bad]})


# ---------------------------------------------------------------- the api section

def test_api_section_is_strict(tmp_path):
    api = _load(tmp_path, """
        api:
          auth: proxy
          proxy: { trusted_from: [10.0.0.0/8], user_header: X-User, tenant_header: X-Tenant, roles_header: X-Roles }
          request_llm: true
          cors_origins: [https://app.example.com]
        """).api
    assert (api.auth, api.proxy.trusted_from, api.proxy.tenant_header, api.request_llm) == (
        "proxy", ["10.0.0.0/8"], "X-Tenant", True)

    defaults = Api()
    assert defaults.auth == "none"
    assert defaults.proxy.trusted_from == ["127.0.0.1/32", "::1/128"]
    assert (defaults.proxy.user_header, defaults.proxy.tenant_header, defaults.proxy.roles_header) == (
        "X-Forwarded-User", None, "X-NL2SQL-Roles")
    assert (defaults.static_role, defaults.request_llm) == (None, False)

    with pytest.raises(ValidationError):
        Api(auth="basic")
    with pytest.raises(ValidationError):
        Api(proxy={"user_heder": "X"})
    with pytest.raises(ConfigError, match="Did you mean 'request_llm'"):
        _load(tmp_path, "api: { request_llms: true }\n")


# ---------------------------------------------------------------- the model override

def test_llm_override_holds_keys_and_models_and_never_prints_a_key():
    override = LLMOverride(keys={"anthropic": "sk-secret-123"},
                           models={"ast_planner": ("anthropic", "claude-opus-5")})
    assert override.keys["anthropic"] == "sk-secret-123"
    assert override.models["ast_planner"] == ("anthropic", "claude-opus-5")
    assert "sk-secret-123" not in repr(override) and "sk-secret-123" not in str(override)
    assert LLMOverride().keys == {} and LLMOverride().models == {}


# ---------------------------------------------------------------- approvals in the state store

def _record(request_id, *, status="pending", days_ago=0.0):
    at = (datetime(2026, 10, 30, tzinfo=timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {"id": request_id, "status": status, "created_at": at, "updated_at": at, "request": {"id": request_id}}


@pytest.fixture
def store():
    state = SqliteState(":memory:")
    state.migrate()
    yield state
    state.close()


def test_state_store_round_trips_approvals(store):
    assert store.get_approval("r1") is None
    store.put_approval(_record("r1"))
    store.put_approval(_record("r2", status="granted", days_ago=1))
    assert store.get_approval("r1")["request"] == {"id": "r1"}
    assert [r["id"] for r in store.list_approvals()] == ["r1", "r2"]
    assert [r["id"] for r in store.list_approvals("pending")] == ["r1"]

    store.put_approval({**store.get_approval("r1"), "status": "denied"})  # replaces
    assert store.get_approval("r1")["status"] == "denied"
    assert len(store.list_approvals()) == 2

    cutoff = datetime(2026, 10, 30, tzinfo=timezone.utc) - timedelta(hours=12)
    assert store.purge_approvals(cutoff) == 1
    assert store.get_approval("r2") is None and store.get_approval("r1") is not None


def test_decide_approval_decides_once(store):
    assert store.decide_approval("nope", "granted", by="ada") is None
    store.put_approval(_record("r1"))
    at = datetime(2026, 10, 31, tzinfo=timezone.utc)
    decided = store.decide_approval("r1", "granted", by="ada", at=at)
    assert (decided["status"], decided["decided_by"], decided["updated_at"]) == ("granted", "ada", at.isoformat())
    assert store.get_approval("r1") == decided
    # a decided request is never overwritten: a late deny returns the stored grant, unchanged
    assert store.decide_approval("r1", "denied", by="bob") == decided
    assert store.get_approval("r1")["status"] == "granted"


def test_approvals_migration_is_registered():
    assert MIGRATIONS[-1][0] == "0007_approvals"


def test_settings_approvals_expire_after_24_hours_by_default():
    settings = Config.model_fields["settings"].default_factory()
    assert settings.approvals.max_age_hours == 24
    with pytest.raises(ValidationError):
        type(settings).model_validate({"approvals": {"max_age_hours": 0}})


# ---------------------------------------------------------------- the mode key

def test_mode_defaults_to_your_data(tmp_path):
    assert _load(tmp_path).mode == "your_data"
    for value in ("demo", "your_data", "hosted"):
        assert _load(tmp_path, f"mode: {value}\n").mode == value


def test_mode_rejects_an_unknown_value(tmp_path):
    with pytest.raises(ConfigError, match="mode"):
        _load(tmp_path, "mode: staging\n")
    with pytest.raises(ValueError, match="staging"):
        resolve_mode(None, "staging", "your_data")


def test_mode_precedence_is_flag_then_env_then_file():
    assert resolve_mode("demo", "hosted", "your_data") == "demo"
    assert resolve_mode(None, "hosted", "demo") == "hosted"
    assert resolve_mode(None, None, "demo") == "demo"
    assert resolve_mode(None, "", "demo") == "demo"  # an empty variable is unset
    assert resolve_mode(None, None, "your_data") == "your_data"


# ---------------------------------------------------------------- exports

def test_shapes_are_exported_from_the_facade():
    for name in ("NL2SQLError", "NotAuthorized", "ApprovalNotFound", "LLMOverride", "resolve_mode",
                 "DashboardBusy", "MissingProviderKey"):
        assert getattr(nl2sql, name) is not None, name
