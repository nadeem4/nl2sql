"""The trace file: redaction, capping, naming, lookup and when it is written."""
from __future__ import annotations

import json
import re

import pytest

from nl2sql.tracing.document import (
    TRUNCATED,
    Limits,
    Redactor,
    cap,
    find_trace,
    should_write,
    trace_filename,
    validate_trace_id,
    write_trace,
)

FAKE_KEY = "sk-test-not-a-real-key-1234"


def test_redacts_known_secret_values_anywhere_in_a_string():
    redactor = Redactor(secrets=["hunter2-long-secret"])
    out = redactor.redact({"note": "the value hunter2-long-secret leaked", "n": 3})
    assert "hunter2-long-secret" not in json.dumps(out)
    assert out["n"] == 3


def test_redacts_secret_shaped_strings_without_being_told_the_value():
    redactor = Redactor(secrets=[])
    # Assembled at run time: these are fake, and a literal credential-shaped
    # string in the source would (rightly) trip the repository's secret scanner.
    token, password, conn_password = "fake" + "token" * 3, "fake" + "Pw1", "fake" + "pass9"
    doc = {
        "a": f"key {FAKE_KEY} here",
        "b": "Authorization: " + "Bearer " + token,
        "c": "postgresql://analyst:" + conn_password + "@db.internal:5432/sales",
        "d": "Server=x;Database=y;User Id=u;" + "Password=" + password + ";",
    }
    text = json.dumps(redactor.redact(doc))
    for leaked in (FAKE_KEY, token, conn_password, password):
        assert leaked not in text
    # What is left is still readable: the user and host survive.
    assert "postgresql://analyst:" in text and "@db.internal" in text


def test_redacts_values_under_secret_named_keys_but_not_token_counts():
    redactor = Redactor(secrets=[])
    out = redactor.redact({"api_key": "plain-value", "password": "x", "input_tokens": 12,
                           "nested": [{"client_secret": "abc"}]})
    assert out["api_key"] == "[REDACTED]"
    assert out["password"] == "[REDACTED]"
    assert out["nested"][0]["client_secret"] == "[REDACTED]"
    assert out["input_tokens"] == 12


def test_caps_long_strings_with_a_marker():
    limits = Limits(sample_rows=50, max_field_chars=100, max_list_items=200)
    out = cap({"s": "x" * 250}, limits)
    assert out["s"].startswith("x" * 100)
    assert TRUNCATED in out["s"] and "150" in out["s"]


def test_caps_rows_to_the_sample_size_and_says_how_many_were_dropped():
    limits = Limits(sample_rows=50, max_field_chars=1000, max_list_items=200)
    out = cap({"rows": [[i] for i in range(120)], "other": list(range(120))}, limits)
    assert out["rows"][:50] == [[i] for i in range(50)]
    assert len(out["rows"]) == 51 and TRUNCATED in out["rows"][-1] and "70" in out["rows"][-1]
    # Lists that are not rows only hit the (larger) list cap.
    assert out["other"] == list(range(120))


def test_caps_terminal_results_as_rows():
    limits = Limits(sample_rows=2, max_field_chars=1000, max_list_items=200)
    out = cap({"terminal_results": {"n1": [{"a": 1}, {"a": 2}, {"a": 3}]}}, limits)
    assert out["terminal_results"]["n1"][:2] == [{"a": 1}, {"a": 2}]
    assert TRUNCATED in out["terminal_results"]["n1"][2]


def test_file_name_is_a_sortable_timestamp_then_the_trace_id():
    name = trace_filename("0b8f7d2e-1111-4222-8333-944455556666")
    assert re.fullmatch(r"\d{8}T\d{6}\d{6}Z_0b8f7d2e-1111-4222-8333-944455556666\.json", name)


@pytest.mark.parametrize("mode,failed,expected", [
    ("off", True, False), ("off", False, False),
    ("on_failure", True, True), ("on_failure", False, False),
    ("always", True, True), ("always", False, True),
])
def test_when_a_trace_is_written(mode, failed, expected):
    assert should_write(mode, failed) is expected


def test_write_then_find_by_id(tmp_path):
    path = write_trace({"trace_id": "abc-123", "x": 1}, tmp_path)
    assert path.parent == tmp_path
    assert find_trace("abc-123", tmp_path) == path
    assert json.loads(path.read_text(encoding="utf-8"))["x"] == 1


@pytest.mark.parametrize("bad", [
    "../etc/passwd", "..", "a/b", "a\\b", "C:\\Windows\\win.ini", "/etc/passwd",
    "", "x" * 200, "abc.json", "*",
])
def test_trace_ids_are_validated_strictly(bad):
    with pytest.raises(ValueError):
        validate_trace_id(bad)


def test_find_never_leaves_the_directory(tmp_path):
    inside = tmp_path / "traces"
    inside.mkdir()
    (tmp_path / "20260101T000000000000Z_secret.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        find_trace("../secret", inside)
    assert find_trace("secret", inside) is None
