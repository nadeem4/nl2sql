"""The test bed's own scripts: the wide schema, the samples and the CI gate.

docs/architecture/phase2.md, section 3. These need no database: they check
the files the compose file loads and the decision the ``realdb-gate`` job makes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

TESTBED = Path(__file__).resolve().parents[4] / "testbed"


@pytest.fixture
def testbed(monkeypatch):
    """The ``testbed/`` folder importable, as it is when the ``samples`` service runs a script there."""
    monkeypatch.syspath_prepend(str(TESTBED))
    import gate
    import samples
    import wide_schema

    return type("Testbed", (), {"gate": gate, "samples": samples, "wide_schema": wide_schema})


def test_wide_schema_is_deterministic(testbed):
    ws = testbed.wide_schema
    for dialect in ws.DIALECTS:
        assert ws.render(ws.build(7), dialect) == ws.render(ws.build(7), dialect)
    assert ws.render(ws.build(7), "postgres") != ws.render(ws.build(8), "postgres")


def test_wide_schema_has_1200_tables_in_20_schemas(testbed):
    ws = testbed.wide_schema
    schemas = ws.build(7)
    numbered = [s for s in schemas if s.name != "audit"]
    assert [s.name for s in numbered] == ws.schema_names()
    assert len(numbered) == 20 and numbered[0].name == "sales_01" and numbered[-1].name == "ops_20"
    assert sum(len(s.tables) for s in numbered) == 1200
    assert "audit" in [s.name for s in schemas]

    tables = [t for s in schemas for t in s.tables]
    assert all(5 <= len(t.columns) <= 30 for t in tables)
    assert all(len(s.views) == 2 for s in schemas)
    assert sorted(t.rows for t in tables if t.rows) == [1_000] * 50 + [1_000_000] * 3
    assert sum(1 for t in tables if t.name != t.name.lower()) == 10
    assert {c.kind for t in tables for c in t.columns} >= set(ws.ODD_KINDS)
    assert any(c.references for t in tables for c in t.columns)
    commented = sum(1 for t in tables if t.comment) / len(tables)
    assert 0.2 < commented < 0.4

    # Every table reaches each server's file, MySQL's in its own databases.
    for dialect in ws.DIALECTS:
        assert ws.render(schemas, dialect).count("CREATE TABLE ") == len(tables)
    mysql = ws.render(schemas, "mysql")
    assert "CREATE DATABASE `wide_s01`;" in mysql and "CREATE DATABASE `wide_s20`;" in mysql


def _pinned(content: bytes, name: str = "a.sql"):
    import samples

    return samples.Sample(name, f"https://example.invalid/{name}", hashlib.sha256(content).hexdigest())


def test_samples_reject_a_bad_checksum(testbed, tmp_path):
    sample = _pinned(b"the real file")

    def tampered(url, target):
        target.write_bytes(b"something else")

    with pytest.raises(testbed.samples.ChecksumError, match="a.sql"):
        testbed.samples.fetch(sample, tmp_path, tampered)
    assert list(tmp_path.iterdir()) == []


def test_samples_skip_what_is_there(testbed, tmp_path):
    content = b"the real file"
    sample = _pinned(content)
    calls = []

    def get(url, target):
        calls.append(url)
        target.write_bytes(content)

    testbed.samples.prepare(tmp_path, [sample], get)
    first = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    assert set(first) == {"a.sql", "wide_postgres.sql", "wide_mysql.sql", "wide_tsql.sql"}

    testbed.samples.prepare(tmp_path, [sample], get)
    assert calls == [sample.url]
    assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == first


@pytest.mark.parametrize("changes, realdb, passes", [
    ("success", "success", True),
    ("success", "skipped", True),    # nothing database-facing changed
    ("success", "failure", False),
    ("success", "cancelled", False),
    ("failure", "skipped", False),   # the paths were never worked out
])
def test_gate_passes_on_skip_and_fails_on_failure(testbed, capsys, changes, realdb, passes):
    code = testbed.gate.main(["--changes", changes, "--realdb", realdb])
    assert (code == 0) is passes
    assert realdb in capsys.readouterr().out
