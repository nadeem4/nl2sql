"""Tier 2 result records, and the pages ``nl2sql benchmark publish`` builds from them.

Every tier 2 run writes one record per config to ``benchmarks/results/``:
``<YYYY-MM-DD>_<engine-version>_<config>.json``, with ``-2``, ``-3``... when
that name is taken. A record holds when the run was, on which engine and
dataset (the gold file's sha256, so runs on different gold sets are never
read as comparable), which model each node ran on, the headline metrics and
the config's full scoreboard (answer faithfulness included; records written
before it existed show a dash). The owner commits records.

``nl2sql benchmark retrieval --record`` writes a retrieval recall record to
``benchmarks/retrieval/``, named ``<YYYY-MM-DD>_<engine-version>_retrieval.json``.

``publish`` reads every record and writes ``docs/benchmarks.md`` (every run,
newest first, plus the latest run per config, then the retrieval recall
runs) and the block between the README's ``BENCHMARKS`` markers. It reads no clock and calls nothing, so the
same records always give byte-identical pages.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import pathlib
import re
import subprocess
from typing import Any, Dict, List, Optional, Sequence

RESULTS_DIR = pathlib.Path("benchmarks") / "results"
RETRIEVAL_DIR = pathlib.Path("benchmarks") / "retrieval"
HISTORY_PATH = pathlib.Path("docs") / "benchmarks.md"
README_PATH = pathlib.Path("README.md")
START = "<!-- BENCHMARKS:START -->"
END = "<!-- BENCHMARKS:END -->"
EMPTY = "No benchmark runs recorded yet."
SCHEMA_VERSION = 1


def installed_engine_version() -> str:
    try:
        return importlib.metadata.version("nl2sql-engine")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def current_git_commit() -> Optional[str]:
    """The checked-out commit, or None outside a git checkout."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def dataset_id(path: pathlib.Path) -> Dict[str, str]:
    """The gold file's name and sha256, of its LF bytes: a Windows checkout's CRLF gives the same hash."""
    data = pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")
    return {"name": pathlib.Path(path).name, "sha256": hashlib.sha256(data).hexdigest()}


def make_record(board: Dict[str, Any], name: str, *, recorded_at: dt.datetime, engine_version: str,
                git_commit: Optional[str]) -> Dict[str, Any]:
    """The record for config ``name`` of a tier 2 scoreboard."""
    cfg = board["configs"][name]
    dataset = pathlib.Path(board["dataset"])
    cases = cfg["completed_cases"]
    tokens = cfg["tokens_by_node"].values()

    def per_case(field: str) -> Optional[float]:
        return round(sum(t[field] for t in tokens) / cases, 1) if cases else None

    return {
        "schema": SCHEMA_VERSION,
        "recorded_at": recorded_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_version": engine_version,
        "git_commit": git_commit,
        "dataset": dataset_id(dataset),
        "config": {"name": name, "models": cfg["models"]},
        "roles": board["roles"],
        "passes": board["passes"],
        "metrics": {
            "cases": cases,
            "accuracy": cfg["accuracy"]["overall"],
            "answerability_precision": cfg["answerability"]["precision"],
            "answerability_recall": cfg["answerability"]["recall"],
            "cost_total": cfg["cost"]["total"],
            "cost_per_question": cfg["cost"]["per_question"],
            "tokens_per_question": {"input": per_case("input_tokens"), "cached": per_case("cached_input_tokens"),
                                    "output": per_case("output_tokens")},
            "latency_p50": cfg["latency"]["question"]["p50"],
            "latency_p95": cfg["latency"]["question"]["p95"],
            "determinism": (cfg["determinism"] or {}).get("share"),
            "faithfulness": (cfg.get("faithfulness") or {}).get("rate"),
        },
        "stopped": board["stopped"],
        "partial": bool(board["stopped"]) or cases < cfg["planned_cases"],
        "scoreboard": cfg,
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "config"


def write_records(board: Dict[str, Any], results_dir: pathlib.Path = RESULTS_DIR, *,
                  recorded_at: Optional[dt.datetime] = None, engine_version: Optional[str] = None,
                  git_commit: Optional[str] = None) -> List[pathlib.Path]:
    """Writes one record per config in ``board``; returns the paths written."""
    recorded_at = recorded_at or dt.datetime.now(dt.timezone.utc)
    version = engine_version or installed_engine_version()
    results_dir = pathlib.Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in board["configs"]:
        stem = f"{recorded_at.astimezone(dt.timezone.utc):%Y-%m-%d}_{_slug(version)}_{_slug(name)}"
        path, n = results_dir / f"{stem}.json", 1
        while path.exists():
            n += 1
            path = results_dir / f"{stem}-{n}.json"
        record = make_record(board, name, recorded_at=recorded_at, engine_version=version, git_commit=git_commit)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n",
                        encoding="utf-8", newline="\n")
        written.append(path)
    return written


def write_retrieval_record(report: Dict[str, Any], results_dir: pathlib.Path = RETRIEVAL_DIR, *,
                           recorded_at: Optional[dt.datetime] = None, engine_version: Optional[str] = None,
                           git_commit: Optional[str] = None) -> pathlib.Path:
    """Writes a retrieval recall report's record, ``<date>_<version>_retrieval.json``; returns its path."""
    recorded_at = recorded_at or dt.datetime.now(dt.timezone.utc)
    version = engine_version or installed_engine_version()
    dataset = pathlib.Path(report["dataset"])
    record = {
        "schema": SCHEMA_VERSION, "kind": "retrieval",
        "recorded_at": recorded_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_version": version, "git_commit": git_commit,
        "dataset": dataset_id(dataset),
        "settings": report["settings"], "metrics": report["summary"],
        # The dataset is named above; its local path would differ on every machine.
        "report": {k: v for k, v in report.items() if k != "dataset"},
    }
    results_dir = pathlib.Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{recorded_at.astimezone(dt.timezone.utc):%Y-%m-%d}_{_slug(version)}_retrieval"
    path, n = results_dir / f"{stem}.json", 1
    while path.exists():
        n += 1
        path = results_dir / f"{stem}-{n}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def load_records(results_dir: pathlib.Path = RESULTS_DIR) -> List[Dict[str, Any]]:
    """Every record in ``results_dir``, newest first (ties broken by file name)."""
    results_dir = pathlib.Path(results_dir)
    if not results_dir.is_dir():
        return []
    loaded = []
    for path in results_dir.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        record["_file"] = path.name
        loaded.append(record)
    return _newest_first(loaded)


def _newest_first(recs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(recs, key=lambda r: (r["recorded_at"], r.get("_file", "")), reverse=True)


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.1%}"


def _usd(v: Optional[float]) -> str:
    return "-" if v is None else f"${v:.4f}"


def _sec(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.2f}s"


def _tokens(t: Dict[str, Any]) -> str:
    return " / ".join("-" if t.get(k) is None else f"{t[k]:,.0f}" for k in ("input", "cached", "output"))


def _models(models: Dict[str, str]) -> str:
    by_model: Dict[str, List[str]] = {}
    for node, model in models.items():
        by_model.setdefault(model, []).append(node)
    return "; ".join(f"{m} ({', '.join(sorted(n))})" for m, n in sorted(by_model.items()))


def _dataset(r: Dict[str, Any]) -> str:
    return f"{r['dataset']['name']} @{r['dataset']['sha256'][:8]}"


def _status(r: Dict[str, Any]) -> str:
    return f"partial ({r['stopped']})" if r.get("stopped") else ("partial" if r.get("partial") else "complete")


def _table(header: Sequence[str], rows: List[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _latest_per_config(recs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The newest record for each (config, dataset) pair, sorted by config name."""
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in _newest_first(recs):
        latest.setdefault((r["config"]["name"], r["dataset"]["sha256"]), r)
    return [latest[k] for k in sorted(latest)]


def render_retrieval(recs: Sequence[Dict[str, Any]]) -> str:
    """The retrieval recall section: every recorded run, newest first; empty without runs."""
    if not recs:
        return ""
    table = _table(
        ["Date (UTC)", "Version", "Commit", "Dataset", "Search", "Embedding", "Questions", "Table recall",
         "Column recall", "Tables / columns sent"],
        [[r["recorded_at"].replace("T", " ").rstrip("Z"), r["engine_version"], (r.get("git_commit") or "-")[:8],
          _dataset(r), f"k {r['settings']['table_k']} tables / {r['settings']['planning_k']} planning",
          r["settings"]["embedding"], r["metrics"]["questions"], _pct(r["metrics"]["table_recall"]),
          _pct(r["metrics"]["column_recall"]), f"{r['metrics']['tables_sent']:.1f} / {r['metrics']['columns_sent']:.1f}"]
         for r in _newest_first(recs)])
    return ("\n## Retrieval recall\n\n"
            "`nl2sql benchmark retrieval --record`: the share of each answerable gold question's needed\n"
            "tables and columns that schema retrieval sends the planner, with the vector search forced on\n"
            "and no LLM. Mean over questions; tables and columns sent are means too.\n\n"
            f"{table}\n")


def render_history(recs: Sequence[Dict[str, Any]], retrieval: Sequence[Dict[str, Any]] = ()) -> str:
    """``docs/benchmarks.md``: the latest run per config, every run newest first, then retrieval recall."""
    head = ("# Benchmark Results\n\n"
            "Tier 2 runs of the engine on the Chinook gold questions with a real model, one row per\n"
            "run and LLM config. Generated by `nl2sql benchmark publish` from the records in\n"
            "`benchmarks/results/`; do not edit by hand. Runs on a different gold dataset (another\n"
            "`@sha`) are not comparable. See [the evaluation dataset](testing/evaluation-dataset.md).\n")
    if not recs:
        return f"{head}\n{EMPTY}\n{render_retrieval(retrieval)}"
    latest = _table(
        ["Config", "Dataset", "Date (UTC)", "Version", "Accuracy", "$/question", "Tokens/question (in / cached / out)",
         "p50", "Determinism", "Faithfulness"],
        [[r["config"]["name"], _dataset(r), r["recorded_at"][:10], r["engine_version"],
          _pct(r["metrics"]["accuracy"]), _usd(r["metrics"]["cost_per_question"]),
          _tokens(r["metrics"]["tokens_per_question"]), _sec(r["metrics"]["latency_p50"]),
          _pct(r["metrics"]["determinism"]), _pct(r["metrics"].get("faithfulness"))]
         for r in _latest_per_config(recs)])
    runs = _table(
        ["Date (UTC)", "Version", "Commit", "Config", "Models", "Dataset", "Roles", "Passes", "Accuracy",
         "Answerability P / R", "$/question", "Tokens/question (in / cached / out)", "p50", "p95", "Determinism",
         "Faithfulness", "Status"],
        [[r["recorded_at"].replace("T", " ").rstrip("Z"), r["engine_version"], (r.get("git_commit") or "-")[:8],
          r["config"]["name"], _models(r["config"]["models"]), _dataset(r), ", ".join(r["roles"] or []),
          r["passes"], _pct(r["metrics"]["accuracy"]),
          f"{_pct(r['metrics']['answerability_precision'])} / {_pct(r['metrics']['answerability_recall'])}",
          _usd(r["metrics"]["cost_per_question"]), _tokens(r["metrics"]["tokens_per_question"]),
          _sec(r["metrics"]["latency_p50"]), _sec(r["metrics"]["latency_p95"]), _pct(r["metrics"]["determinism"]),
          _pct(r["metrics"].get("faithfulness")), _status(r)] for r in _newest_first(recs)])
    return f"{head}\n## Latest per config\n\n{latest}\n\n## All runs\n\n{runs}\n{render_retrieval(retrieval)}"


def render_readme_block(recs: Sequence[Dict[str, Any]]) -> str:
    """The README block: the latest run per config, and a link to the full history."""
    if not recs:
        return f"{START}\n{EMPTY}\n{END}"
    table = _table(["Date (UTC)", "Version", "Config", "Accuracy", "$/question"],
                   [[r["recorded_at"][:10], r["engine_version"], r["config"]["name"], _pct(r["metrics"]["accuracy"]),
                     _usd(r["metrics"]["cost_per_question"])] for r in _latest_per_config(recs)])
    return f"{START}\n{table}\n\nFull history: [docs/benchmarks.md](docs/benchmarks.md)\n{END}"


def replace_block(text: str, block: str) -> str:
    """``text`` with everything from START to END (inclusive) replaced by ``block``."""
    start, end = text.find(START), text.find(END)
    if start < 0 or end < start:
        raise ValueError(f"README has no {START} ... {END} block to replace.")
    return text[:start] + block + text[end + len(END):]


def publish(results_dir: pathlib.Path = RESULTS_DIR, history_path: pathlib.Path = HISTORY_PATH,
            readme_path: pathlib.Path = README_PATH, retrieval_dir: pathlib.Path = RETRIEVAL_DIR) -> int:
    """Rewrites the history page and the README block from the records; returns how many there are."""
    recs = load_records(results_dir)
    retrieval = load_records(retrieval_dir)
    history_path = pathlib.Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(render_history(recs, retrieval), encoding="utf-8", newline="\n")
    readme_path = pathlib.Path(readme_path)
    readme_path.write_text(replace_block(readme_path.read_text(encoding="utf-8"), render_readme_block(recs)),
                           encoding="utf-8", newline="\n")
    return len(recs) + len(retrieval)
