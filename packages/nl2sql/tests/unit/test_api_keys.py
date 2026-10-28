"""``engine.api_keys`` (docs/architecture/wave_c.md, section 9, row M1; D127, D130)."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import pytest

from nl2sql import NL2SQL, UserContext
from nl2sql.api import api_keys_api as api_keys_module
from nl2sql.policies import keys as token_format
from nl2sql.testing.config import make_config

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "Reads", "allow": ["*"]}}
ADMIN = UserContext(user_id="root", roles=["admin"])
ANALYST = UserContext(user_id="ana", roles=["analyst"])


@pytest.fixture
def engine(tmp_path):
    with NL2SQL(make_config(tmp_path, policies={"roles": ROLES, "admin_roles": ["admin"]})) as built:
        yield built


def _make(engine, **kwargs):
    return engine.api_keys.create(user_id="ana", roles=["analyst"], by=ADMIN, **kwargs)


def test_token_is_returned_once_and_only_its_hash_is_stored(engine):
    key, token = _make(engine, tenant_id="acme", label="laptop")

    assert token.startswith(f"nl2sql_{key.id}_") and len(key.id) == 8
    secret = token.split("_", 2)[2]
    row = engine._runtime.state.get_api_key(key.id)
    assert row["hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in str(row) and token not in str(row)
    listed = engine.api_keys.list(by=ADMIN)
    assert [k.id for k in listed] == [key.id] and token not in repr(listed) and secret not in repr(listed)
    assert (key.user_id, key.tenant_id, key.roles, key.label) == ("ana", "acme", ["analyst"], "laptop")
    assert key.created_by == "root" and key.revoked_at is None


def test_verify_returns_the_keys_user_context(engine):
    key, token = _make(engine, tenant_id="acme")

    who = engine.api_keys.verify(token)

    assert who == UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])


def test_two_tokens_are_different_and_each_verifies_as_its_own_key(engine):
    first, first_token = _make(engine)
    second, second_token = engine.api_keys.create(user_id="bob", roles=["admin"], by=ADMIN)

    assert first.id != second.id and first_token != second_token
    assert engine.api_keys.verify(second_token).user_id == "bob"
    assert engine.api_keys.verify(first_token).user_id == "ana"


def test_revoked_key_does_not_verify(engine):
    key, token = _make(engine)

    engine.api_keys.revoke(key.id, by=ADMIN)

    assert engine.api_keys.verify(token) is None
    assert engine.api_keys.list(by=ADMIN)[0].revoked_at is not None


def test_revoking_twice_keeps_the_first_time_and_publishes_once(engine):
    events = []
    engine.events.subscribe(events.append, ["api_key.revoked"])
    key, _ = _make(engine)

    engine.api_keys.revoke(key.id, by=ADMIN)
    first = engine.api_keys.list(by=ADMIN)[0].revoked_at
    engine.api_keys.revoke(key.id, by=ADMIN)

    assert engine.api_keys.list(by=ADMIN)[0].revoked_at == first and len(events) == 1


def test_revoking_an_unknown_id_is_a_value_error(engine):
    with pytest.raises(ValueError, match="No API key"):
        engine.api_keys.revoke("deadbeef", by=ADMIN)


@pytest.mark.parametrize("token", [
    "", "nope", "nl2sql_", "nl2sql__", "nl2sql_abc_def", "Bearer x", None, 42,
    "nl2sql_" + "0" * 8 + "_" + "A" * 42,    # a secret one character short
    "nl2sql_" + "0" * 8 + "_" + "A" * 44,    # one character long
    "nl2sql_" + "0" * 8 + "_" + "A" * 42 + "!",
    "nl2sql_" + "g" * 8 + "_" + "A" * 43,    # an id that is not hex
])
def test_malformed_token_does_not_verify(engine, token):
    _make(engine)

    assert engine.api_keys.verify(token) is None


def test_a_token_with_trailing_text_does_not_verify(engine):
    _, token = _make(engine)

    assert engine.api_keys.verify(token + "\n") is None and engine.api_keys.verify(token + " ") is None
    assert engine.api_keys.verify(token) is not None


def test_unknown_id_and_wrong_secret_do_not_verify(engine):
    key, token = _make(engine)
    secret = token.split("_", 2)[2]
    other = ("B" if secret[0] != "B" else "C") + secret[1:]

    assert engine.api_keys.verify(f"nl2sql_{key.id}_{other}") is None
    stranger = "0" * 8 if key.id != "0" * 8 else "1" * 8
    assert engine.api_keys.verify(f"nl2sql_{stranger}_{secret}") is None


def test_verify_compares_in_constant_time(engine, monkeypatch):
    _, token = _make(engine)
    seen = []
    real = token_format.hmac.compare_digest
    monkeypatch.setattr(token_format.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))

    assert engine.api_keys.verify(token) is not None and len(seen) == 1


def test_api_keys_require_a_caller(engine):
    with pytest.raises(TypeError):
        engine.api_keys.create(user_id="ana", roles=["analyst"])
    with pytest.raises(TypeError):
        engine.api_keys.list()
    with pytest.raises(TypeError):
        engine.api_keys.revoke("deadbeef")
    for call in (lambda: engine.api_keys.create(user_id="ana", roles=["analyst"], by=None),
                 lambda: engine.api_keys.list(by="admin"),
                 lambda: engine.api_keys.revoke("deadbeef", by={"roles": ["admin"]})):
        with pytest.raises(TypeError, match="UserContext"):
            call()
    assert engine._runtime.state.list_api_keys() == []


def test_only_admins_manage_keys(engine):
    key, _ = _make(engine)

    with pytest.raises(PermissionError, match="admin"):
        engine.api_keys.create(user_id="x", roles=["analyst"], by=ANALYST)
    with pytest.raises(PermissionError):
        engine.api_keys.list(by=ANALYST)
    with pytest.raises(PermissionError):
        engine.api_keys.revoke(key.id, by=ANALYST)
    with pytest.raises(PermissionError):
        engine.api_keys.list(by=UserContext(user_id="nobody", roles=[]))

    assert len(engine._runtime.state.list_api_keys()) == 1
    assert engine._runtime.state.get_api_key(key.id)["revoked_at"] is None


def test_under_allow_all_every_caller_may_manage_keys_but_a_key_carries_no_role(tmp_path):
    with NL2SQL(make_config(tmp_path)) as engine:
        key, token = engine.api_keys.create(user_id="ana", roles=[], by=UserContext(roles=[]))

        assert engine.api_keys.verify(token).roles == [] and key.created_by is None


def test_under_allow_all_a_role_name_is_refused(tmp_path):
    # Keys outlive the policy: an "admin" key minted now would be a real admin once roles are configured.
    with NL2SQL(make_config(tmp_path)) as engine:
        with pytest.raises(ValueError, match="admin"):
            engine.api_keys.create(user_id="ana", roles=["admin"], by=UserContext(roles=[]))
        assert engine._runtime.state.list_api_keys() == []


def test_key_roles_must_be_configured(engine):
    with pytest.raises(ValueError, match="ghost"):
        engine.api_keys.create(user_id="ana", roles=["analyst", "ghost"], by=ADMIN)
    assert engine._runtime.state.list_api_keys() == []


def test_a_key_needs_a_user(engine):
    with pytest.raises(ValueError, match="user_id"):
        engine.api_keys.create(user_id=" ", roles=["analyst"], by=ADMIN)


def test_key_events_never_carry_the_token(engine, caplog):
    events = []
    engine.events.subscribe(events.append, ["api_key.created", "api_key.revoked"])
    caplog.set_level(logging.DEBUG)

    key, token = _make(engine, tenant_id="acme")
    engine.api_keys.verify(token)
    engine.api_keys.revoke(key.id, by=ADMIN)

    assert [e.name for e in events] == ["api_key.created", "api_key.revoked"]
    secret = token.split("_", 2)[2]
    for event in events:
        assert event.data == {"key_id": key.id, "user_id": "ana", "tenant_id": "acme", "roles": ["analyst"],
                              "by": "root"}
        assert token not in str(event) and secret not in str(event)
    assert secret not in caplog.text and token not in caplog.text


def test_a_refused_call_publishes_nothing(engine):
    events = []
    engine.events.subscribe(events.append, ["api_key.created", "api_key.revoked"])

    with pytest.raises(PermissionError):
        engine.api_keys.create(user_id="x", roles=["analyst"], by=ANALYST)
    with pytest.raises(ValueError):
        engine.api_keys.create(user_id="x", roles=["ghost"], by=ADMIN)

    assert events == []


def test_an_id_clash_on_create_retries_with_a_new_id(engine, monkeypatch):
    first, first_token = _make(engine)
    ids = iter([first.id, first.id, "cafebabe"])
    monkeypatch.setattr(token_format, "new_id", lambda: next(ids))

    second, second_token = engine.api_keys.create(user_id="bob", roles=["analyst"], by=ADMIN)

    assert second.id == "cafebabe"
    assert engine.api_keys.verify(first_token).user_id == "ana"  # the first key was not overwritten
    assert engine.api_keys.verify(second_token).user_id == "bob"


def test_an_id_clash_that_never_clears_fails_loudly(engine, monkeypatch):
    first, _ = _make(engine)
    monkeypatch.setattr(token_format, "new_id", lambda: first.id)

    with pytest.raises(RuntimeError, match="id"):
        _make(engine)

    assert len(engine._runtime.state.list_api_keys()) == 1


def test_list_is_newest_first_and_includes_revoked(engine, monkeypatch):
    ticks = iter(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=n) for n in range(100))

    class Clock(datetime):  # each call to now() is one second later, so no two keys share a time
        @classmethod
        def now(cls, tz=None):
            return next(ticks)

    monkeypatch.setattr(api_keys_module, "datetime", Clock)
    a, _ = _make(engine)
    b, _ = engine.api_keys.create(user_id="bob", roles=["admin"], by=ADMIN)
    engine.api_keys.revoke(a.id, by=ADMIN)

    listed = engine.api_keys.list(by=ADMIN)

    assert [k.id for k in listed] == [b.id, a.id]
    assert listed[0].revoked_at is None and listed[1].revoked_at is not None


def test_keys_survive_a_new_engine(tmp_path):
    config = make_config(tmp_path, policies={"roles": ROLES, "admin_roles": ["admin"]})
    with NL2SQL(config) as first:
        _, token = _make(first)
    with NL2SQL(config) as second:
        assert second.api_keys.verify(token).user_id == "ana"
