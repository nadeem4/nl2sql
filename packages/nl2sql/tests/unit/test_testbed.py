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
    import passwords
    import samples
    import wide_schema

    return type("Testbed", (), {"gate": gate, "passwords": passwords, "samples": samples, "wide_schema": wide_schema})


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
    # A foreign key's column has its parent's id type: MySQL and SQL Server refuse int against bigint.
    for schema in schemas:
        ids = {t.name: t.columns[0].kind for t in schema.tables}
        assert all(c.kind == ids[c.references] for t in schema.tables for c in t.columns if c.references)
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


def test_passwords_are_random_and_pass_sql_servers_policy(testbed, capsys):
    import re

    assert testbed.passwords.main([]) == 0
    first = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert set(first) == {"TESTBED_POSTGRES_PASSWORD", "TESTBED_MYSQL_PASSWORD", "TESTBED_MSSQL_PASSWORD"}
    testbed.passwords.main([])
    second = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert not set(first.values()) & set(second.values())
    for value in first.values():
        assert len(value) >= 20 and re.fullmatch(r"[A-Za-z0-9_-]+", value)
        assert re.search("[A-Z]", value) and re.search("[a-z]", value) and re.search("[0-9]", value)


def test_the_compose_file_holds_no_password(testbed):
    """Every password is required from the environment: no default, so no literal in the repository."""
    import re

    compose = (TESTBED / "docker-compose.yml").read_text(encoding="utf-8")
    uses = re.findall(r"\$\{(TESTBED_\w+_PASSWORD)([^}]*)\}", compose)
    assert {name for name, _ in uses} == {"TESTBED_POSTGRES_PASSWORD", "TESTBED_MYSQL_PASSWORD",
                                          "TESTBED_MSSQL_PASSWORD"}
    assert all(rest.startswith(":?") for _, rest in uses), uses


def test_up_wait_survives_a_repeat_start():
    """``up -d --wait`` fails on a container that has exited, unless a dependent waits for it to complete.

    On a repeat start a one-shot that skips its work exits at once, so every
    service is either awaited with ``service_completed_successfully`` or has a
    healthcheck, which a container passes only while it runs.
    """
    import yaml

    services = yaml.safe_load((TESTBED / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    awaited = {name for service in services.values() for name, dependency in service.get("depends_on", {}).items()
               if dependency.get("condition") == "service_completed_successfully"}
    unchecked = [name for name, service in services.items() if name not in awaited and "healthcheck" not in service]
    assert not unchecked, unchecked


def test_mssql_init_marks_itself_loaded_and_keeps_running():
    """Its healthcheck reads the mark init.sh writes last, and the script then idles instead of exiting."""
    import yaml

    init = yaml.safe_load((TESTBED / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["mssql-init"]
    assert "/tmp/loaded" in " ".join(init["healthcheck"]["test"])
    assert init.get("init") is True                       # `sleep` as PID 1 would ignore SIGTERM
    lines = [line for line in (TESTBED / "mssql" / "init.sh").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert lines[-2:] == ["touch /tmp/loaded", "exec sleep infinity"]
