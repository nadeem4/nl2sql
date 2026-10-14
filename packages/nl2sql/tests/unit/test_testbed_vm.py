"""``scripts/testbed.py``: the GCP test-bed VM, driven through a fake ``gcloud``.

docs/architecture/phase2.md, section 3, "The GCP VM". A fake ``gcloud`` on
``PATH`` records every call (its arguments, its stdin and the test-bed
and gcloud variables in its environment) and plays the parts that matter: it
prints a password for ``secrets versions access``, a state for ``instances
describe``, and for ``start-iap-tunnel`` it listens on the local port until it
is killed. ``FAKE_GCLOUD_FLAKY=scp`` makes the first ``FAKE_GCLOUD_FLAKY_TIMES``
``scp`` calls fail the way plink does while the SSH key propagates. No cloud
resource is touched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "testbed.py"

FAKE_GCLOUD = textwrap.dedent('''
    import json, os, socket, sys, time

    argv = sys.argv[1:]
    record = {"argv": argv,
              "env": {k: v for k, v in os.environ.items()
                      if k.startswith(("TESTBED_", "REALDB_", "CLOUDSDK_SSH_"))}}
    if "ssh" in argv:
        record["stdin"] = sys.stdin.read()
    with open(os.environ["FAKE_GCLOUD_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\\n")

    flaky = os.environ.get("FAKE_GCLOUD_FLAKY")
    if flaky and flaky in argv:
        counter = os.environ["FAKE_GCLOUD_LOG"] + ".flaky"
        failed = int(open(counter).read()) if os.path.exists(counter) else 0
        if failed < int(os.environ.get("FAKE_GCLOUD_FLAKY_TIMES", "1")):
            open(counter, "w").write(str(failed + 1))
            sys.stderr.write("FATAL ERROR: Remote side unexpectedly closed network connection\\n")
            sys.exit(1)

    fail = os.environ.get("FAKE_GCLOUD_FAIL")
    if fail and fail in argv:
        sys.stderr.write("fake gcloud: " + fail + " failed\\n")
        sys.exit(1)
    if argv[:3] == ["secrets", "versions", "access"]:
        secret = next(a.split("=", 1)[1] for a in argv if a.startswith("--secret="))
        sys.stdout.write("Pw:" + secret + "/@%#")
    elif argv[:3] == ["compute", "instances", "describe"]:
        sys.stdout.write(os.environ.get("FAKE_GCLOUD_STATE", "TERMINATED") + "\\t2026-10-14T10:00:00.000-07:00\\n")
    elif argv[:2] == ["compute", "start-iap-tunnel"]:
        port = int(next(a for a in argv if a.startswith("--local-host-port=")).rsplit(":", 1)[1])
        server = socket.socket()
        server.bind(("127.0.0.1", port))
        server.listen()
        while True:
            connection, _ = server.accept()
            connection.close()
''')


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def tb(tmp_path, monkeypatch):
    """The script, a fake ``gcloud`` first on ``PATH``, free local ports and a state file in ``tmp_path``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fake_gcloud.py"
    fake.write_text(FAKE_GCLOUD, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "gcloud.cmd").write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8")
    else:
        wrapper = bin_dir / "gcloud"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gcloud.log"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_GCLOUD_LOG", str(log))

    spec = importlib.util.spec_from_file_location("testbed_vm", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "testbed_vm", module)     # dataclasses look the module up
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "TUNNELS", {name: (remote, _free_port())
                                             for name, (remote, _) in module.TUNNELS.items()})
    monkeypatch.setattr(module, "STATE", tmp_path / "state" / "tunnels.json")
    monkeypatch.setattr(module, "CONNECT_DELAY", 0)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    module.calls = calls
    yield module
    module.down(module.Target())          # never leave a fake tunnel listening


PASSWORDS = {name: "Pw:" + secret + "/@%#" for name, secret in
             (("postgres", "testbed-postgres-root-password"), ("mysql", "testbed-mysql-root-password"),
              ("mssql", "testbed-mssql-root-password"))}


def _kinds(calls):
    return [" ".join(c["argv"][:3]) for c in calls]


def _compose(calls):
    return next(c for c in calls if "ssh" in c["argv"] and any("up -d --wait" in a for a in c["argv"]))


def _copies(calls):
    return [c for c in calls if c["argv"][:2] == ["compute", "scp"]]


def _port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def test_up_starts_copies_composes_and_tunnels(tb):
    assert tb.main(["up"]) == 0

    calls = [c for c in tb.calls() if c["argv"][0] == "compute"]
    argv = [c["argv"] for c in calls]
    assert argv[0][:3] == ["compute", "instances", "start"]
    assert argv[1][:2] == ["compute", "scp"] and "--tunnel-through-iap" in argv[1] and "--recurse" in argv[1]
    assert argv[2][:2] == ["compute", "ssh"] and "--tunnel-through-iap" in argv[2]
    assert argv[3][:2] == ["compute", "ssh"] and "--tunnel-through-iap" in argv[3]
    command = next(a for a in argv[3] if "docker compose" in a)
    assert "TESTBED_BIND=0.0.0.0" in command and "up -d --wait" in command
    tunnels = [a for a in argv if a[:2] == ["compute", "start-iap-tunnel"]]
    assert sorted(int(a[3]) for a in tunnels) == [11433, 13306, 15432]
    for _, local in tb.TUNNELS.values():
        assert _port_answers(local)
    for a in argv:
        assert "--project=nl2sql-testbed-261014" in a


def test_passwords_reach_compose_and_the_child_only(tb, tmp_path, capfd):
    before = dict(os.environ)
    out = tmp_path / "child_env.json"
    child = [sys.executable, "-c",
             "import json, os, sys; json.dump({k: v for k, v in os.environ.items() if k.startswith('REALDB_')},"
             " open(sys.argv[1], 'w'))", str(out)]

    assert tb.main(["run", "--", *child]) == 0

    calls = tb.calls()
    compose = _compose(calls)
    assert compose["stdin"].splitlines() == [PASSWORDS["postgres"], PASSWORDS["mysql"], PASSWORDS["mssql"]]
    for c in calls:
        for password in PASSWORDS.values():
            assert password not in " ".join(c["argv"])
            assert password not in json.dumps(c["env"])

    child_env = json.loads(out.read_text())
    ports = {name: local for name, (_, local) in tb.TUNNELS.items()}
    assert child_env == {
        "REALDB_POSTGRES": f"postgresql://postgres:{quote(PASSWORDS['postgres'], safe='')}@127.0.0.1:{ports['postgres']}/",
        "REALDB_MYSQL": f"mysql://root:{quote(PASSWORDS['mysql'], safe='')}@127.0.0.1:{ports['mysql']}/",
        "REALDB_MSSQL": f"mssql://sa:{quote(PASSWORDS['mssql'], safe='')}@127.0.0.1:{ports['mssql']}/",
    }
    assert dict(os.environ) == before
    printed = capfd.readouterr()
    for password in PASSWORDS.values():
        assert password not in printed.out + printed.err
        assert quote(password, safe="") not in printed.out + printed.err
    assert not any(p.is_file() and any(pw in p.read_text(errors="ignore") for pw in PASSWORDS.values())
                   for p in tb.STATE.parent.rglob("*"))


def _stopped(tb):
    calls = tb.calls()
    assert _kinds(calls)[-1] == "compute instances stop"
    for _, local in tb.TUNNELS.values():
        assert not _port_answers(local)
    assert not tb.STATE.exists()


def test_run_stops_even_when_the_command_fails(tb):
    assert tb.main(["run", "--", sys.executable, "-c", "raise SystemExit(3)"]) == 3
    _stopped(tb)


def test_run_stops_on_keyboard_interrupt(tb, monkeypatch):
    def interrupted(command, env):
        raise KeyboardInterrupt

    monkeypatch.setattr(tb, "run_child", interrupted)
    assert tb.main(["run", "--", "pytest"]) == 130
    _stopped(tb)


def test_run_stops_after_a_failed_up(tb, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_GCLOUD_FAIL", "scp")
    ran = []
    monkeypatch.setattr(tb, "run_child", lambda command, env: ran.append(command) or 0)

    assert tb.main(["run", "--", "pytest"]) == 1
    assert ran == []
    assert "scp" in capsys.readouterr().err
    assert len(_copies(tb.calls())) == 1        # not a connection failure: not retried
    _stopped(tb)


def test_every_ssh_and_scp_call_skips_the_host_key_prompt(tb):
    assert tb.main(["up"]) == 0

    remote = [c["argv"] for c in tb.calls() if c["argv"][:2] in (["compute", "ssh"], ["compute", "scp"])]
    assert len(remote) == 3
    for argv in remote:
        assert "--strict-host-key-checking=no" in argv and "--tunnel-through-iap" in argv


def test_compose_reads_its_stdin_through_plink(tb):
    # gcloud on Windows feeds plink a "y" instead of our stdin unless told not to;
    # the copy and the Docker wait keep that answer, which caches the host key.
    assert tb.main(["up"]) == 0

    calls = tb.calls()
    assert _compose(calls)["env"]["CLOUDSDK_SSH_PUTTY_FORCE_CONNECT"] == "false"
    earlier = [c for c in calls if c["argv"][:2] in (["compute", "ssh"], ["compute", "scp"]) and c is not _compose(calls)]
    for c in earlier:
        assert "CLOUDSDK_SSH_PUTTY_FORCE_CONNECT" not in c["env"]


def test_waits_for_docker_before_compose_and_gives_up(tb):
    assert tb.main(["up"]) == 0

    ssh = [c["argv"] for c in tb.calls() if c["argv"][:2] == ["compute", "ssh"]]
    wait = next(a for a in ssh[0] if a.startswith("--command="))
    assert "command -v docker" in wait and "docker compose version" in wait
    assert "seq" in wait and "exit 1" in wait          # bounded, not `until ...; done`
    assert "until" not in next(a for a in ssh[1] if a.startswith("--command="))


def test_a_connection_failure_while_the_key_propagates_is_retried(tb, monkeypatch):
    monkeypatch.setenv("FAKE_GCLOUD_FLAKY", "scp")
    monkeypatch.setenv("FAKE_GCLOUD_FLAKY_TIMES", "2")

    assert tb.main(["up"]) == 0
    assert len(_copies(tb.calls())) == 3


def test_a_persistent_connection_failure_gives_up_and_run_stops_the_vm(tb, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_GCLOUD_FLAKY", "scp")
    monkeypatch.setenv("FAKE_GCLOUD_FLAKY_TIMES", "99")
    ran = []
    monkeypatch.setattr(tb, "run_child", lambda command, env: ran.append(command) or 0)

    assert tb.main(["run", "--", "pytest"]) == 1
    assert len(_copies(tb.calls())) == tb.CONNECT_TRIES == 5
    assert ran == []
    assert "scp" in capsys.readouterr().err
    _stopped(tb)


def test_down_closes_tunnels_and_is_idempotent(tb):
    assert tb.main(["up"]) == 0
    assert tb.STATE.exists()

    assert tb.main(["down"]) == 0
    _stopped(tb)
    assert tb.main(["down"]) == 0
    _stopped(tb)


def test_status_never_prints_a_password(tb, monkeypatch, capfd):
    assert tb.main(["up"]) == 0
    capfd.readouterr()
    monkeypatch.setenv("FAKE_GCLOUD_STATE", "RUNNING")
    accessed = len([c for c in tb.calls() if c["argv"][0] == "secrets"])

    assert tb.main(["status"]) == 0

    printed = capfd.readouterr()
    assert "RUNNING" in printed.out and "running for" in printed.out
    for name in tb.TUNNELS:
        assert f"{name}: open" in printed.out
    for password in PASSWORDS.values():
        assert password not in printed.out + printed.err
    assert len([c for c in tb.calls() if c["argv"][0] == "secrets"]) == accessed
