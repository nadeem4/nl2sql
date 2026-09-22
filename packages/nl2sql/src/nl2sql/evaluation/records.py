"""Benchmark result records, and the pages ``nl2sql benchmark publish`` builds from them.

Every tier 2 run writes one record per config and ``benchmark retrieval
--record`` writes one record, under a ``benchmarks/`` folder::

    benchmarks/<kind>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json

``kind`` is ``tier2`` or ``retrieval``, ``database`` the datasource id and
``shortsha`` the engine commit the run used (``-2``, ``-3``... when the name
is taken). A record holds when the run was, the code (``git_commit`` and
``git_dirty``, resolved from the installed package's own checkout), the gold
dataset (its sha256), the database (datasource id, engine and a schema
fingerprint), an optional ``note``, the headline metrics and the full report.
Records are never edited: writing, ``publish`` and ``publish --from`` all
refuse to overwrite one.

The database is the one the *dataset* names, not everything the context has
registered: the demo registers three databases and the gold set asks about
one, so a run there is still filed under ``chinook`` with Chinook's
fingerprint, and stays comparable with the runs recorded before the other
two existed.

Two runs are comparable when they share the kind, the dataset sha and the
schema fingerprint (and, for tier 2, the roles). ``publish`` groups the
history by kind and database, lists runs newest first with the change
against the previous comparable run of the same config, and marks a run that
starts a new series. It reads no clock and calls nothing, so the same
records always give byte-identical pages.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import pathlib
import re
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

BENCHMARKS_DIR = pathlib.Path("benchmarks")
HISTORY_PATH = pathlib.Path("docs") / "benchmarks.md"
README_PATH = pathlib.Path("README.md")
START = "<!-- BENCHMARKS:START -->"
END = "<!-- BENCHMARKS:END -->"
EMPTY = "No benchmark runs recorded yet."
README_TIER2_HEADING = "### Tier 2: English questions answered correctly"
README_TIER2_INTRO = ("The real model end to end on the Chinook gold questions. Accuracy is the strict share whose "
                      "rows match the gold answer exactly, or that are refused where the gold set expects a "
                      "refusal; lenient allows extra and reordered columns and date labels; faithfulness is the "
                      "share of written answers whose numbers and names come from the rows.")
TIER2_EMPTY = ("No tier 2 run recorded yet. Run one from a demo folder with "
               "`nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5`, "
               "then `nl2sql benchmark publish --from <demo folder>` from the repo.")
README_RETRIEVAL_HEADING = "### Retrieval recall"
README_RETRIEVAL_INTRO = ("The share of each answerable gold question's needed tables and columns that schema "
                          "retrieval sends the planner, with no LLM involved.")
RETRIEVAL_EMPTY = "No retrieval run recorded yet: `nl2sql --env demo benchmark retrieval --record`."
SCHEMA_VERSION = 2
UNKNOWN = "unknown"
UNKNOWN_DATABASE = {"datasource_id": UNKNOWN, "engine": UNKNOWN, "schema_fingerprint": UNKNOWN,
                    "tables": None, "columns": None}
KIND_TITLES = {"tier2": "Tier 2", "retrieval": "Retrieval recall"}
# The metrics a run is judged by, with how a change in each is written.
HEADLINE = {"tier2": [("accuracy", "accuracy", "pp"), ("lenient_accuracy", "lenient", "pp"),
                      ("faithfulness", "faithfulness", "pp"), ("cost_per_question", "$/question", "usd")],
            "retrieval": [("table_recall", "tables", "pp"), ("column_recall", "columns", "pp")]}


def installed_engine_version() -> str:
    try:
        return importlib.metadata.version("nl2sql-engine")
    except importlib.metadata.PackageNotFoundError:
        return UNKNOWN


def _git(root: pathlib.Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def code_version(start: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """``{git_commit, git_dirty}`` of the checkout the engine is installed from.

    Walks up from ``start`` (the ``nl2sql`` package itself by default) to the
    folder holding ``.git``, so a run from a project folder outside the repo
    still names the engine's commit. ``git_dirty`` ignores untracked files.
    Both are ``"unknown"`` for a wheel install or without git.
    """
    if start is None:
        import nl2sql
        start = pathlib.Path(nl2sql.__file__)
    start = pathlib.Path(start).resolve()
    root = next((d for d in [start, *start.parents] if (d / ".git").exists()), None)
    sha = _git(root, "rev-parse", "HEAD") if root else None
    status = _git(root, "status", "--porcelain", "--untracked-files=no") if sha else None
    if not sha or status is None:
        return {"git_commit": UNKNOWN, "git_dirty": UNKNOWN}
    return {"git_commit": sha.strip(), "git_dirty": bool(status.strip())}


def dataset_id(path: pathlib.Path) -> Dict[str, str]:
    """The gold file's name and sha256, of its LF bytes: a Windows checkout's CRLF gives the same hash."""
    data = pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")
    return {"name": pathlib.Path(path).name, "sha256": hashlib.sha256(data).hexdigest()}


def database_identity(ctx, datasource_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The database a run used: datasource id, engine, a schema fingerprint and the table and column counts.

    The fingerprint hashes the latest indexed schema snapshot (tables,
    columns, types, keys), so re-indexing an unchanged database keeps it.
    Several datasources are joined with ``+``.

    ``datasource_ids`` are the ones the run actually used -- the gold
    dataset's, from :func:`nl2sql.evaluation.gold.dataset_datasources` -- and
    every caller passes them. Registering another database beside Chinook
    must not rename the run or move its fingerprint, or the history would
    split into a new series. Without them every registered datasource is
    described, which is right only when the run really used all of them.
    """
    from nl2sql.schema.protocol import generate_schema_fingerprint

    ids = sorted(set(datasource_ids)) if datasource_ids is not None else sorted(ctx.ds_registry.list_ids())
    parts = []
    for ds_id in ids:
        snapshot = ctx.schema_store.get_latest_snapshot(ds_id)
        if snapshot is None:
            parts.append((ds_id, UNKNOWN, UNKNOWN, None, None))
            continue
        contract = snapshot.contract
        parts.append((ds_id, str(contract.engine_type), generate_schema_fingerprint(contract)[:16],
                      len(contract.tables), sum(len(t.columns) for t in contract.tables.values())))
    if not parts:
        return dict(UNKNOWN_DATABASE)
    fingerprints = [p[2] for p in parts]
    fingerprint = (fingerprints[0] if len(parts) == 1 else UNKNOWN if UNKNOWN in fingerprints
                   else hashlib.sha256("+".join(fingerprints).encode()).hexdigest()[:16])
    counts = [p[3] for p in parts], [p[4] for p in parts]
    return {"datasource_id": "+".join(p[0] for p in parts),
            "engine": "+".join(sorted({p[1] for p in parts})),
            "schema_fingerprint": fingerprint,
            "tables": None if None in counts[0] else sum(counts[0]),
            "columns": None if None in counts[1] else sum(counts[1])}


def _stamp(recorded_at: dt.datetime) -> str:
    return recorded_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _common(kind: str, *, recorded_at: dt.datetime, engine_version: str, code: Dict[str, Any],
            dataset: pathlib.Path, database: Optional[Dict[str, Any]], note: Optional[str]) -> Dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "kind": kind, "recorded_at": _stamp(recorded_at),
            "engine_version": engine_version, "git_commit": code.get("git_commit") or UNKNOWN,
            "git_dirty": code.get("git_dirty", UNKNOWN), "note": note or None,
            "dataset": dataset_id(dataset), "database": database or dict(UNKNOWN_DATABASE)}


def make_record(board: Dict[str, Any], name: str, *, recorded_at: dt.datetime, engine_version: str,
                code: Dict[str, Any], note: Optional[str] = None) -> Dict[str, Any]:
    """The record for config ``name`` of a tier 2 scoreboard."""
    cfg = board["configs"][name]
    cases = cfg["completed_cases"]
    tokens = cfg["tokens_by_node"].values()

    def per_case(field: str) -> Optional[float]:
        return round(sum(t[field] for t in tokens) / cases, 1) if cases else None

    return {
        **_common("tier2", recorded_at=recorded_at, engine_version=engine_version, code=code,
                  dataset=pathlib.Path(board["dataset"]), database=board.get("database"), note=note),
        "config": {"name": name, "models": cfg["models"]},
        "roles": board["roles"],
        "passes": board["passes"],
        "metrics": {
            "cases": cases,
            "accuracy": cfg["accuracy"]["overall"],
            "lenient_accuracy": cfg["accuracy"].get("lenient"),
            "accuracy_interval": cfg["accuracy"].get("interval"),
            "lenient_accuracy_interval": cfg["accuracy"].get("lenient_interval"),
            "pass_k": cfg["accuracy"].get("pass_k"),
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


def make_retrieval_record(report: Dict[str, Any], *, recorded_at: dt.datetime, engine_version: str,
                          code: Dict[str, Any], note: Optional[str] = None) -> Dict[str, Any]:
    """The record of a retrieval recall report."""
    return {
        **_common("retrieval", recorded_at=recorded_at, engine_version=engine_version, code=code,
                  dataset=pathlib.Path(report["dataset"]), database=report.get("database"), note=note),
        "config": {"name": "retrieval"},
        "settings": report["settings"], "metrics": report["summary"],
        # The dataset is named above; its local path would differ on every machine.
        "report": {k: v for k, v in report.items() if k not in ("dataset", "database")},
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "config"


def record_relpath(record: Dict[str, Any]) -> pathlib.Path:
    """Where a record lives under ``benchmarks/``, without a clash suffix."""
    sha = record.get("git_commit") or UNKNOWN
    stem = f"{record['recorded_at'][:10]}_{sha[:7] if sha != UNKNOWN else UNKNOWN}_{_slug(record['config']['name'])}"
    return pathlib.Path(record["kind"]) / _slug(record["database"]["datasource_id"]) / f"{stem}.json"


def _dump(record: Dict[str, Any]) -> str:
    return json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n"


def _write_new(benchmarks_dir: pathlib.Path, record: Dict[str, Any]) -> pathlib.Path:
    """Writes ``record`` to a new file, never over an existing one; returns its path."""
    rel = record_relpath(record)
    folder = pathlib.Path(benchmarks_dir) / rel.parent
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        path = folder / (rel.name if n == 1 else f"{rel.stem}-{n}.json")
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as f:
                f.write(_dump(record))
            return path
        except FileExistsError:
            n += 1


def write_records(board: Dict[str, Any], benchmarks_dir: pathlib.Path = BENCHMARKS_DIR, *,
                  recorded_at: Optional[dt.datetime] = None, engine_version: Optional[str] = None,
                  code: Optional[Dict[str, Any]] = None, note: Optional[str] = None) -> List[pathlib.Path]:
    """Writes one record per config in ``board``; returns the paths written."""
    recorded_at = recorded_at or dt.datetime.now(dt.timezone.utc)
    version = engine_version or installed_engine_version()
    code = code if code is not None else code_version()
    return [_write_new(benchmarks_dir, make_record(board, name, recorded_at=recorded_at, engine_version=version,
                                                   code=code, note=note))
            for name in board["configs"]]


def write_retrieval_record(report: Dict[str, Any], benchmarks_dir: pathlib.Path = BENCHMARKS_DIR, *,
                           recorded_at: Optional[dt.datetime] = None, engine_version: Optional[str] = None,
                           code: Optional[Dict[str, Any]] = None, note: Optional[str] = None) -> pathlib.Path:
    """Writes a retrieval recall report's record; returns its path."""
    record = make_retrieval_record(report, recorded_at=recorded_at or dt.datetime.now(dt.timezone.utc),
                                   engine_version=engine_version or installed_engine_version(),
                                   code=code if code is not None else code_version(), note=note)
    return _write_new(benchmarks_dir, record)


def _record_files(benchmarks_dir: pathlib.Path) -> List[pathlib.Path]:
    """Every ``<kind>/<database>/*.json`` under ``benchmarks_dir``."""
    return sorted(pathlib.Path(benchmarks_dir).glob("*/*/*.json"))


def load_records(benchmarks_dir: pathlib.Path = BENCHMARKS_DIR) -> List[Dict[str, Any]]:
    """Every record under ``benchmarks_dir``, newest first (ties broken by file name)."""
    loaded = []
    for path in _record_files(benchmarks_dir):
        record = json.loads(path.read_text(encoding="utf-8"))
        record["_file"] = path.relative_to(benchmarks_dir).as_posix()
        loaded.append(record)
    return _newest_first(loaded)


def import_records(sources: Sequence[pathlib.Path], benchmarks_dir: pathlib.Path = BENCHMARKS_DIR
                   ) -> Dict[str, List[str]]:
    """Copies records from other projects' ``benchmarks/`` folders into ``benchmarks_dir``.

    A source is a project folder (its ``benchmarks/`` is read) or a
    ``benchmarks/`` folder itself. A record already present with the same
    bytes is skipped; one with the same path and other content is a conflict
    and is left alone. Records from before the current layout are skipped.
    Returns ``{"copied": [...], "identical": [...], "conflicts": [...], "old_format": [...]}``.
    """
    outcome: Dict[str, List[str]] = {"copied": [], "identical": [], "conflicts": [], "old_format": []}
    for source in sources:
        source = pathlib.Path(source)
        root = source / "benchmarks" if (source / "benchmarks").is_dir() else source
        for path in sorted(root.rglob("*.json")):
            data = path.read_bytes()
            try:
                record = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or record.get("schema", 1) < SCHEMA_VERSION:
                outcome["old_format"].append(str(path))
                continue
            target = pathlib.Path(benchmarks_dir) / record_relpath(record).parent / path.name
            rel = target.relative_to(benchmarks_dir).as_posix()
            if target.exists():
                same = target.read_bytes().replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n")
                outcome["identical" if same else "conflicts"].append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as f:
                f.write(data)
            outcome["copied"].append(rel)
    return outcome


def _newest_first(recs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(recs, key=lambda r: (r["recorded_at"], r.get("_file", "")), reverse=True)


# -- comparing runs ---------------------------------------------------------

def line_key(r: Dict[str, Any]) -> Tuple[str, str, str]:
    """The runs a record is compared with over time: same kind, database and config."""
    return r["kind"], r["database"]["datasource_id"], r["config"]["name"]


def series_key(r: Dict[str, Any]) -> Tuple:
    """What must match for two runs to be comparable: dataset, schema, and for tier 2 the roles."""
    roles = tuple(r.get("roles") or []) if r["kind"] == "tier2" else ()
    return r["kind"], r["dataset"]["sha256"], r["database"]["schema_fingerprint"], roles


def comparable(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return series_key(a) == series_key(b)


def previous_runs(recs: Sequence[Dict[str, Any]]) -> Dict[int, Optional[Dict[str, Any]]]:
    """For each record (by ``id``), the run just before it on the same kind, database and config."""
    previous: Dict[int, Optional[Dict[str, Any]]] = {}
    last: Dict[Tuple, Dict[str, Any]] = {}
    for r in reversed(_newest_first(recs)):
        previous[id(r)] = last.get(line_key(r))
        last[line_key(r)] = r
    return previous


def deltas(new: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """The change in each headline metric, ``new`` minus ``old``; None where either is missing."""
    out = {}
    for field, _, _ in HEADLINE[new["kind"]]:
        a, b = new["metrics"].get(field), old["metrics"].get(field)
        out[field] = None if a is None or b is None else round(a - b, 6)
    return out


def _series_break(new: Dict[str, Any], old: Dict[str, Any]) -> str:
    changed = [label for label, same in (
        ("dataset", new["dataset"]["sha256"] == old["dataset"]["sha256"]),
        ("schema", new["database"]["schema_fingerprint"] == old["database"]["schema_fingerprint"]),
        ("roles", series_key(new)[3] == series_key(old)[3])) if not same]
    return f"new series ({', '.join(changed)} changed)"


def flips(new: Dict[str, Any], old: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The questions that flipped between two tier 2 records, and McNemar's p-value.

    Read from each record's committed scoreboard, so the history page can say
    how many questions actually moved rather than only how the headline did.
    None for a record with no per-question results (a retrieval run, or one
    written before they were kept).
    """
    from nl2sql.evaluation.tier2 import question_flips

    now = (new.get("scoreboard") or {}).get("results")
    before = (old.get("scoreboard") or {}).get("results")
    if not now or not before:
        return None
    return question_flips(now, before)


def describe_change(new: Dict[str, Any], old: Optional[Dict[str, Any]], *, show_flips: bool = False) -> str:
    """The Δ cell: the change against the previous run, or why there is none.

    With ``show_flips`` the headline deltas are followed by how many questions
    flipped each way and McNemar's exact p-value on them, which is what says
    whether a difference of a few points means anything at n = 43.
    """
    if old is None:
        return "first run"
    if not comparable(new, old):
        return _series_break(new, old)
    parts = []
    for field, label, unit in HEADLINE[new["kind"]]:
        d = deltas(new, old)[field]
        if d is None:
            continue
        parts.append(f"{label} {d * 100:+.1f} pp" if unit == "pp"
                     else f"{label} {'+' if d >= 0 else '-'}${abs(d):.4f}")
    moved = flips(new, old) if show_flips else None
    if moved is not None:
        lost, gained = len(moved["pass_to_fail"]), len(moved["fail_to_pass"])
        parts.append(f"{lost} flipped to fail, {gained} to pass (McNemar p={moved['p_value']:.3f})")
    return ", ".join(parts) or "-"


# -- rendering --------------------------------------------------------------

def _pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.1%}"


def _ci(value: Optional[float], interval: Optional[Sequence[float]]) -> str:
    """``58.1% [43.3-71.6]``: the share with its 95% interval, or just the share."""
    if value is None or not interval:
        return _pct(value)
    return f"{value:.1%} [{interval[0]:.1%}-{interval[1]:.1%}]".replace("%]", "]").replace("%-", "-")


def _usd(v: Optional[float]) -> str:
    return "-" if v is None else f"${v:.4f}"


def _passes(r: Dict[str, Any]) -> str:
    """``3 (pass^3 61.0%)``: how many passes, and the share of questions that passed every one."""
    pass_k = (r["metrics"].get("pass_k") or {}) if r["kind"] == "tier2" else {}
    return f"{r['passes']} (pass^{pass_k['k']} {_pct(pass_k['strict'])})" if pass_k else str(r["passes"])


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


def _schema(r: Dict[str, Any]) -> str:
    fp = r["database"]["schema_fingerprint"]
    return fp if fp == UNKNOWN else f"@{fp[:8]}"


def _commit(r: Dict[str, Any]) -> str:
    sha = r.get("git_commit") or UNKNOWN
    return UNKNOWN if sha == UNKNOWN else sha[:7] + (" (dirty)" if r.get("git_dirty") is True else "")


def _when(r: Dict[str, Any]) -> str:
    return r["recorded_at"].replace("T", " ").rstrip("Z")


def _status(r: Dict[str, Any]) -> str:
    return f"partial ({r['stopped']})" if r.get("stopped") else ("partial" if r.get("partial") else "complete")


def _table(header: Sequence[str], rows: List[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _latest_per_line(recs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The newest record for each (kind, database, config), in that order."""
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in _newest_first(recs):
        latest.setdefault(line_key(r), r)
    order = list(KIND_TITLES)
    return [latest[k] for k in sorted(latest, key=lambda k: (order.index(k[0]) if k[0] in order else 99, k))]


def _groups(recs: Sequence[Dict[str, Any]]) -> List[Tuple[Tuple[str, str], List[Dict[str, Any]]]]:
    """Records grouped by (kind, database), tier 2 first, each group newest first."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in _newest_first(recs):
        groups.setdefault((r["kind"], r["database"]["datasource_id"]), []).append(r)
    order = list(KIND_TITLES)
    return sorted(groups.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 99, kv[0]))


def _group_heading(kind: str, group: List[Dict[str, Any]]) -> str:
    db = group[0]["database"]
    size = "" if db.get("tables") is None else f", {db['tables']} tables / {db['columns']} columns"
    return f"## {KIND_TITLES.get(kind, kind)}: {db['datasource_id']} ({db['engine']}{size})"


def _tier2_section(group: List[Dict[str, Any]], previous) -> str:
    latest = _table(
        ["Config", "Date (UTC)", "Commit", "Accuracy (strict, 95% CI)", "Accuracy (lenient, 95% CI)",
         "Faithfulness", "$/question", "Δ vs previous", "Note"],
        [[r["config"]["name"], r["recorded_at"][:10], _commit(r),
          _ci(r["metrics"]["accuracy"], r["metrics"].get("accuracy_interval")),
          _ci(r["metrics"].get("lenient_accuracy"), r["metrics"].get("lenient_accuracy_interval")),
          _pct(r["metrics"].get("faithfulness")), _usd(r["metrics"]["cost_per_question"]),
          describe_change(r, previous[id(r)], show_flips=True), r.get("note") or "-"]
         for r in _latest_per_line(group)])
    runs = _table(
        ["Date (UTC)", "Commit", "Note", "Config", "Models", "Dataset", "Schema", "Roles", "Passes",
         "Accuracy (strict, 95% CI)", "Accuracy (lenient, 95% CI)",
         "Faithfulness", "$/question", "Δ vs previous", "Answerability P / R",
         "Tokens/question (in / cached / out)", "p50", "p95", "Determinism", "Status"],
        [[_when(r), _commit(r), r.get("note") or "-", r["config"]["name"], _models(r["config"]["models"]),
          _dataset(r), _schema(r), ", ".join(r["roles"] or []), _passes(r),
          _ci(r["metrics"]["accuracy"], r["metrics"].get("accuracy_interval")),
          _ci(r["metrics"].get("lenient_accuracy"), r["metrics"].get("lenient_accuracy_interval")),
          _pct(r["metrics"].get("faithfulness")), _usd(r["metrics"]["cost_per_question"]),
          describe_change(r, previous[id(r)], show_flips=True),
          f"{_pct(r['metrics']['answerability_precision'])} / {_pct(r['metrics']['answerability_recall'])}",
          _tokens(r["metrics"]["tokens_per_question"]), _sec(r["metrics"]["latency_p50"]),
          _sec(r["metrics"]["latency_p95"]), _pct(r["metrics"]["determinism"]), _status(r)] for r in group])
    return f"### Latest per config\n\n{latest}\n\n### All runs\n\n{runs}\n"


def _retrieval_section(group: List[Dict[str, Any]], previous) -> str:
    runs = _table(
        ["Date (UTC)", "Commit", "Note", "Dataset", "Schema", "Search", "Embedding", "Questions", "Table recall",
         "Column recall", "Δ vs previous", "Tables / columns sent"],
        [[_when(r), _commit(r), r.get("note") or "-", _dataset(r), _schema(r),
          f"k {r['settings']['table_k']} tables / {r['settings']['planning_k']} planning",
          r["settings"]["embedding"], r["metrics"]["questions"], _pct(r["metrics"]["table_recall"]),
          _pct(r["metrics"]["column_recall"]), describe_change(r, previous[id(r)]),
          f"{r['metrics']['tables_sent']:.1f} / {r['metrics']['columns_sent']:.1f}"] for r in group])
    return ("`nl2sql benchmark retrieval --record`: the share of each answerable gold question's needed\n"
            "tables and columns that schema retrieval sends the planner, with the vector search forced on\n"
            "and no LLM. Mean over questions; tables and columns sent are means too.\n\n"
            f"{runs}\n")


def render_history(recs: Sequence[Dict[str, Any]]) -> str:
    """``docs/benchmarks.md``: per kind and database, the latest runs and every run newest first."""
    head = ("# Benchmark Results\n\n"
            "Recorded benchmark runs of the engine on the Chinook gold questions. Generated by\n"
            "`nl2sql benchmark publish` from the records in `benchmarks/`; do not edit by hand.\n"
            "Grouped by benchmark and database, newest first. Accuracy is given with its 95% Wilson\n"
            "interval. \"Δ vs previous\" is the change against the previous run of the same config, and\n"
            "for tier 2 how many questions flipped each way with McNemar's exact p-value on them: at 43\n"
            "questions a few points either way is noise, so read the flips and the p-value rather than\n"
            "the headline. Runs on another gold dataset (`@sha`), another schema or, for tier 2, other\n"
            "roles are not comparable and start a new series. See\n"
            "[the evaluation dataset](testing/evaluation-dataset.md).\n")
    if not recs:
        return f"{head}\n{EMPTY}\n"
    previous = previous_runs(recs)
    sections = []
    for (kind, _), group in _groups(recs):
        body = _tier2_section(group, previous) if kind == "tier2" else _retrieval_section(group, previous)
        sections.append(f"{_group_heading(kind, group)}\n\n{body}")
    return head + "\n" + "\n".join(sections)


def render_readme_block(recs: Sequence[Dict[str, Any]]) -> str:
    """The README block: tier 2 accuracy then retrieval recall, latest run per database and config.

    Each benchmark gets its own heading and table, and says so when it has no run yet.
    """
    previous = previous_runs(recs) if recs else {}
    latest = _latest_per_line(recs)
    tier2 = [r for r in latest if r["kind"] == "tier2"]
    retrieval = [r for r in latest if r["kind"] == "retrieval"]
    tier2_body = _table(
        ["Config", "Database", "Date (UTC)", "Commit", "Accuracy (strict)", "Accuracy (lenient)",
         "Faithfulness", "$/question", "Δ vs previous"],
        [[r["config"]["name"], r["database"]["datasource_id"], r["recorded_at"][:10], _commit(r),
          _ci(r["metrics"]["accuracy"], r["metrics"].get("accuracy_interval")),
          _pct(r["metrics"].get("lenient_accuracy")),
          _pct(r["metrics"].get("faithfulness")),
          _usd(r["metrics"]["cost_per_question"]), describe_change(r, previous[id(r)])] for r in tier2],
    ) if tier2 else TIER2_EMPTY
    retrieval_body = _table(
        ["Database", "Date (UTC)", "Commit", "Table recall", "Column recall", "Δ vs previous"],
        [[r["database"]["datasource_id"], r["recorded_at"][:10], _commit(r), _pct(r["metrics"]["table_recall"]),
          _pct(r["metrics"]["column_recall"]), describe_change(r, previous[id(r)])] for r in retrieval],
    ) if retrieval else RETRIEVAL_EMPTY
    parts = [README_TIER2_HEADING, README_TIER2_INTRO, tier2_body,
             README_RETRIEVAL_HEADING, README_RETRIEVAL_INTRO, retrieval_body,
             "Full history: [docs/benchmarks.md](docs/benchmarks.md)"]
    return START + "\n" + "\n\n".join(parts) + "\n" + END


def replace_block(text: str, block: str) -> str:
    """``text`` with everything from START to END (inclusive) replaced by ``block``."""
    start, end = text.find(START), text.find(END)
    if start < 0 or end < start:
        raise ValueError(f"README has no {START} ... {END} block to replace.")
    return text[:start] + block + text[end + len(END):]


def publish(benchmarks_dir: pathlib.Path = BENCHMARKS_DIR, history_path: pathlib.Path = HISTORY_PATH,
            readme_path: pathlib.Path = README_PATH) -> int:
    """Rewrites the history page and the README block from the records; returns how many there are.

    Reads the records only; it never writes one.
    """
    recs = load_records(benchmarks_dir)
    history_path = pathlib.Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(render_history(recs), encoding="utf-8", newline="\n")
    readme_path = pathlib.Path(readme_path)
    readme_path.write_text(replace_block(readme_path.read_text(encoding="utf-8"), render_readme_block(recs)),
                           encoding="utf-8", newline="\n")
    return len(recs)
