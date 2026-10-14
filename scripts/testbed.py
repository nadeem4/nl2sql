"""Run the real-database test bed on its GCP VM, started for a session and stopped after it.

    python scripts/testbed.py up                    # start the VM, compose up, open the IAP tunnels
    python scripts/testbed.py run -- pytest -m realdb
    python scripts/testbed.py status
    python scripts/testbed.py down                  # close the tunnels, stop the VM; safe to repeat

``run`` is ``up``, then the command with ``REALDB_POSTGRES``, ``REALDB_MYSQL``
and ``REALDB_MSSQL`` in its environment, then ``down`` in a ``finally``: also
after Ctrl-C, a failing command or a failed ``up``. It exits with the
command's code.

The passwords come from Secret Manager into memory. They reach the VM on the
stdin of the ``docker compose`` command, and the child through its own
environment; they are never written to a file, put on a command line or
printed, and this process's environment is never changed.

The VM, its firewall rule and the nightly stop job are created once, by hand:
docs/testing/test-bed.md, "The GCP VM". The design is
docs/architecture/phase2.md, section 3.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
TESTBED = REPO / "testbed"

PROJECT = "nl2sql-testbed-261014"
ZONE = "us-central1-a"
INSTANCE = "nl2sql-testbed"

# server: (port on the VM, port on this machine)
TUNNELS: Dict[str, tuple] = {"postgres": (15432, 15432), "mysql": (13306, 13306), "mssql": (11433, 11433)}
SECRETS = {"postgres": "testbed-postgres-root-password", "mysql": "testbed-mysql-root-password",
           "mssql": "testbed-mssql-root-password"}
URLS = {"postgres": "postgresql://postgres:{password}@127.0.0.1:{port}/",
        "mysql": "mysql://root:{password}@127.0.0.1:{port}/",
        "mssql": "mssql://sa:{password}@127.0.0.1:{port}/"}

# The tunnels' process ids, so a later `down` can close them. Never a password.
STATE = Path(tempfile.gettempdir()) / "nl2sql-testbed" / "tunnels.json"

RETRIES = 12            # the first ssh after a start waits for the VM to boot
RETRY_DELAY = 10
TUNNEL_TIMEOUT = 60

# Runs on the VM. The passwords arrive on stdin, one per line, and live only in
# this command's environment. The first boot installs Docker, so wait for it.
COMPOSE = ("IFS= read -r TESTBED_POSTGRES_PASSWORD && IFS= read -r TESTBED_MYSQL_PASSWORD"
           " && IFS= read -r TESTBED_MSSQL_PASSWORD"
           " && export TESTBED_POSTGRES_PASSWORD TESTBED_MYSQL_PASSWORD TESTBED_MSSQL_PASSWORD TESTBED_BIND=0.0.0.0"
           " && until sudo docker compose version >/dev/null 2>&1; do sleep 5; done"
           " && sudo -E docker compose -f testbed/docker-compose.yml up -d --wait")


class TestbedError(Exception):
    """A step failed; the message names it and never holds a password."""


@dataclass(frozen=True)
class Target:
    project: str = PROJECT
    zone: str = ZONE
    instance: str = INSTANCE

    @property
    def flags(self) -> List[str]:
        return [f"--project={self.project}", f"--zone={self.zone}"]


def gcloud() -> str:
    found = shutil.which("gcloud")       # finds gcloud.cmd on Windows
    if not found:
        raise TestbedError("gcloud is not on PATH: install the Google Cloud CLI and run `gcloud auth login`.")
    return found


def call(args: Sequence[str], *, input: Optional[str] = None, capture: bool = False) -> str:
    """Run ``gcloud args``; raise ``TestbedError`` naming the step when it fails."""
    result = subprocess.run([gcloud(), *args], input=input, text=True,
                            stdin=None if input is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE if capture else None)
    if result.returncode != 0:
        raise TestbedError(f"`gcloud {' '.join(args[:3])}` failed with exit code {result.returncode}.")
    return result.stdout or ""


def retried(args: Sequence[str], **kwargs) -> str:
    for attempt in range(RETRIES):
        try:
            return call(args, **kwargs)
        except TestbedError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)
    raise AssertionError("unreachable")


def passwords(target: Target) -> Dict[str, str]:
    """The three passwords from Secret Manager, in memory only."""
    return {name: call(["secrets", "versions", "access", "latest", f"--secret={secret}",
                        f"--project={target.project}"], capture=True).rstrip("\r\n")
            for name, secret in SECRETS.items()}


def up(target: Target) -> Dict[str, str]:
    """Start the VM, compose up on it, open the tunnels; return the passwords."""
    secrets = passwords(target)
    print(f"Starting {target.instance} ...", file=sys.stderr)
    call(["compute", "instances", "start", target.instance, *target.flags])
    ssh = ["--tunnel-through-iap", "--quiet", "--strict-host-key-checking=no", *target.flags]
    with tempfile.TemporaryDirectory() as staging:
        # Only the repository's files: never a local testbed/.env.
        copy = Path(staging) / "testbed"
        shutil.copytree(TESTBED, copy, ignore=shutil.ignore_patterns(".env", "__pycache__"))
        print("Copying testbed/ ...", file=sys.stderr)
        retried(["compute", "scp", "--recurse", *ssh, str(copy), f"{target.instance}:."])
    print("Starting the servers (the first time loads the samples, a few minutes) ...", file=sys.stderr)
    call(["compute", "ssh", target.instance, *ssh, f"--command={COMPOSE}"],
         input="".join(secrets[name] + "\n" for name in ("postgres", "mysql", "mssql")))
    open_tunnels(target)
    return secrets


def open_tunnels(target: Target) -> None:
    pids = []
    for remote, local in TUNNELS.values():
        process = subprocess.Popen(
            [gcloud(), "compute", "start-iap-tunnel", target.instance, str(remote),
             f"--local-host-port=localhost:{local}", *target.flags],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **_detached())
        pids.append(process.pid)
        _save(pids)
    deadline = time.monotonic() + TUNNEL_TIMEOUT
    for name, (_, local) in TUNNELS.items():
        while not answers(local):
            if time.monotonic() > deadline:
                raise TestbedError(f"The {name} tunnel did not open on localhost:{local}.")
            time.sleep(0.2)
    print("Tunnels open: " + ", ".join(f"{n} localhost:{p}" for n, (_, p) in TUNNELS.items()), file=sys.stderr)


def down(target: Target) -> None:
    """Close the tunnels this script opened and stop the VM. Safe to repeat."""
    for pid in _load():
        _kill(pid)
    STATE.unlink(missing_ok=True)
    call(["compute", "instances", "stop", target.instance, *target.flags])


def realdb_environment(secrets: Dict[str, str]) -> Dict[str, str]:
    return {f"REALDB_{name.upper()}": URLS[name].format(password=quote(secrets[name], safe=""), port=local)
            for name, (_, local) in TUNNELS.items()}


def run_child(command: Sequence[str], env: Dict[str, str]) -> int:
    executable = shutil.which(command[0]) or command[0]
    return subprocess.call([executable, *command[1:]], env=env)


def run(target: Target, command: Sequence[str]) -> int:
    try:
        secrets = up(target)
        return run_child(command, {**os.environ, **realdb_environment(secrets)})
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except TestbedError as error:
        print(f"testbed: {error}", file=sys.stderr)
        return 1
    finally:
        print(f"Stopping {target.instance} ...", file=sys.stderr)
        down(target)


def status(target: Target) -> None:
    line = call(["compute", "instances", "describe", target.instance, *target.flags,
                 "--format=value(status,lastStartTimestamp)"], capture=True).strip()
    state, _, started = line.partition("\t")
    text = f"{target.instance}: {state}"
    if state == "RUNNING" and started:
        running = datetime.now(timezone.utc) - datetime.fromisoformat(started.strip())
        hours, minutes = divmod(int(running.total_seconds()) // 60, 60)
        text += f", running for {hours}h {minutes:02d}m"
    print(text)
    for name, (_, local) in TUNNELS.items():
        print(f"{name}: {'open' if answers(local) else 'closed'} (localhost:{local})")


def answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _detached() -> dict:
    """Tunnels outlive `up`, so they leave this process's group and console."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def _kill(pid: int) -> None:
    if os.name == "nt":       # gcloud.cmd runs Python under cmd.exe: kill the tree
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _save(pids: List[int]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"pids": pids}), encoding="utf-8")


def _load() -> List[int]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))["pids"]
    except (OSError, ValueError, KeyError):
        return []


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--zone", default=ZONE)
    parser.add_argument("--instance", default=INSTANCE)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("up", help="start the VM, compose up and open the tunnels")
    run_parser = commands.add_parser("run", help="up, run a command, then down (always)")
    run_parser.add_argument("command", nargs=argparse.REMAINDER, help="-- the command to run")
    commands.add_parser("down", help="close the tunnels and stop the VM")
    commands.add_parser("status", help="the VM's state and the tunnels")
    args = parser.parse_args(argv)
    target = Target(args.project, args.zone, args.instance)

    try:
        if args.action == "run":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if not command:
                parser.error("run needs a command: run -- pytest -m realdb")
            return run(target, command)
        if args.action == "up":
            up(target)
            print("Run `python scripts/testbed.py down` when you are done: a running VM bills by the hour.",
                  file=sys.stderr)
        elif args.action == "down":
            down(target)
        else:
            status(target)
        return 0
    except TestbedError as error:
        print(f"testbed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
