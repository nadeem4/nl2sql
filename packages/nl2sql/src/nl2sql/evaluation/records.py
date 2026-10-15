"""Eval records, and the pages :func:`publish` builds from them.

``nl2sql eval publish`` writes one record per config of an eval report
(:class:`~nl2sql.evaluation.report.EvalReport`) under a ``benchmarks/``
folder::

    benchmarks/<mode>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json

``mode`` is the run's mode (``full``, ``plans`` or ``retrieval``),
``database`` the datasource id and ``shortsha`` the engine commit the run
used (``-2``, ``-3``... when the name is taken). A record holds when the run
was, the code (``git_commit`` and ``git_dirty``, resolved from the installed
package's own checkout), the suite (``dataset``: its name and digest), the
database (datasource id, engine and a schema fingerprint), an optional
``note``, the headline metrics and the config's board. Records are never
edited: writing and ``publish --from`` both refuse to overwrite one.

A record is committed for good, so it is kept small: the board's case
results keep only what the gate and the pages read (:func:`slim_result`), and
the file is one line of compact JSON. The full report (SQL, plans, tokens,
timings) is ``eval_<mode>.json``, which ``nl2sql eval run`` writes and CI
keeps as an artifact. The 0.6 records are kept as they were written.

The records 0.6's ``nl2sql benchmark`` wrote stay as they are: kind
``tier2`` is a mode full run (:func:`mode_of`) on the 0.6 gold file, and
kind ``retrieval`` a mode retrieval run. The history shows them in the same
tables as the runs after them.

**The headline accuracy is execution accuracy** (``accuracy.execution``:
``result_match`` and ``outcome``, no other scorer), the measure 0.6's tier 2
published, so the series reads the same before and after 0.7. The stricter
``accuracy.overall`` (every scorer, faithfulness included) and
``accuracy.generated`` are kept in the record beside it.

The database is the one the *suite* names, not everything the context has
registered: the demo registers three databases and the Chinook suite asks
about one, so a run there is filed under ``chinook`` with Chinook's
fingerprint.

Two runs are comparable when they share the mode, the suite digest and the
schema fingerprint (and, for mode full, the roles and whether curated
answers were on; a record from before that field ran with them off).
``publish`` groups the history by mode and database, lists runs newest first
with the change against the previous comparable run of the same config, and
marks a run that starts a new series. It reads no clock and calls nothing,
so the same records always give byte-identical pages.
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
EMPTY = "No eval runs recorded yet."
README_FULL_HEADING = "### Mode full: English questions answered correctly"
README_FULL_INTRO = ("The real model end to end on the Chinook suite. Accuracy is execution accuracy, the measure "
                     "0.6's tier 2 published: the strict share whose rows match the expected rows exactly, or that "
                     "are refused where the case expects a refusal (`result_match` and `outcome`; no other scorer "
                     "counts). Lenient allows extra and reordered columns and date labels; faithfulness is the "
                     "share of written answers whose numbers and names come from the rows.")
FULL_EMPTY = ("No mode full run recorded yet. Run one from a demo folder with "
              "`nl2sql eval run --mode full --llm gpt-5.4 --max-cost 5`, "
              "then `nl2sql eval publish <demo folder>/eval_full.json` from the repo.")
README_RETRIEVAL_HEADING = "### Retrieval recall"
README_RETRIEVAL_INTRO = ("The share of each answerable case's needed tables and columns that schema "
                          "retrieval sends the planner, with no LLM involved.")
RETRIEVAL_EMPTY = ("No retrieval run recorded yet: `nl2sql eval run --mode retrieval`, "
                   "then `nl2sql eval publish`.")
SCHEMA_VERSION = 2
UNKNOWN = "unknown"
UNKNOWN_DATABASE = {"datasource_id": UNKNOWN, "engine": UNKNOWN, "schema_fingerprint": UNKNOWN,
                    "tables": None, "columns": None}
KIND_TITLES = {"full": "Mode full", "plans": "Mode plans", "retrieval": "Retrieval recall"}
# The kind 0.6's records were written under, by the mode it is now.
_MODE_OF_KIND = {"tier2": "full"}
# The metrics a run is judged by, with how a change in each is written.
HEADLINE = {"full": [("accuracy", "accuracy", "pp"), ("lenient_accuracy", "lenient", "pp"),
                     ("faithfulness", "faithfulness", "pp"), ("cost_per_question", "$/question", "usd")],
            "plans": [("accuracy", "accuracy", "pp")],
            "retrieval": [("table_recall", "tables", "pp"), ("column_recall", "columns", "pp")]}


def mode_of(record: Dict[str, Any]) -> str:
    """The mode a record's run used: its ``kind``, with 0.6's ``tier2`` read as ``full``."""
    return _MODE_OF_KIND.get(record["kind"], record["kind"])


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


def database_identity(ctx, datasource_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The database a run used: datasource id, engine, a schema fingerprint and the table and column counts.

    The fingerprint hashes the snapshot each live index build was made from (tables,
    columns, types, keys), so re-indexing an unchanged database keeps it.
    Several datasources are joined with ``+``.

    ``datasource_ids`` are the ones the run actually used -- the suites'
    cases' ``datasources`` (``engine.evals.run`` passes them) -- and every
    caller passes them. Registering another database beside Chinook
    must not rename the run or move its fingerprint, or the history would
    split into a new series. Without them every registered datasource is
    described, which is right only when the run really used all of them.
    """
    from nl2sql.schema.fingerprint import generate_schema_fingerprint

    ids = sorted(set(datasource_ids)) if datasource_ids is not None else sorted(ctx.ds_registry.list_ids())
    parts = []
    live = ctx.vector_store
    for ds_id in ids:
        snapshot = live.snapshot(ds_id)
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


def run_stamp(*, recorded_at: Optional[dt.datetime] = None, engine_version: Optional[str] = None,
              code: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """When a run was and which code it ran: ``nl2sql eval run`` writes it beside the report it saves.

    ``publish`` then records the run's own time and commit, not the time it was published.
    """
    code = code if code is not None else code_version()
    return {"recorded_at": _stamp(recorded_at or dt.datetime.now(dt.timezone.utc)),
            "engine_version": engine_version or installed_engine_version(),
            "git_commit": code.get("git_commit") or UNKNOWN, "git_dirty": code.get("git_dirty", UNKNOWN)}


def _report_dict(report: Any) -> Dict[str, Any]:
    return report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)


def _share(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _execution_metrics(board: Dict[str, Any], passes: int) -> Dict[str, Any]:
    """The headline numbers of a mode full or plans board: execution accuracy first (0.6's measure)."""
    from nl2sql.evaluation.report import execution_passed, reliable, wilson_interval

    results = board.get("results") or []
    cases = board["completed_cases"]
    tokens = board["tokens_by_node"].values()
    lenient = sum(execution_passed(r, lenient=True) for r in results)
    bounds = wilson_interval(lenient, len(results))
    pass_k = None
    if passes > 1:
        strict = reliable(results, passed=execution_passed)
        loose = reliable(results, passed=lambda r: execution_passed(r, lenient=True))
        pass_k = {"k": passes, "questions": len(strict), "strict": _share(sum(strict.values()), len(strict)),
                  "lenient": _share(sum(loose.values()), len(loose))}

    def per_case(field: str) -> Optional[float]:
        return round(sum(t[field] for t in tokens) / cases, 1) if cases else None

    return {
        "cases": cases,
        "accuracy": board["accuracy"]["execution"],
        "lenient_accuracy": _share(lenient, len(results)),
        "accuracy_interval": board["accuracy"].get("execution_interval"),
        "lenient_accuracy_interval": None if bounds is None else [round(b, 4) for b in bounds],
        "accuracy_all_scorers": board["accuracy"]["overall"],
        "accuracy_generated": board["accuracy"].get("generated"),
        "pass_k": pass_k,
        "answerability_precision": board["answerability"]["precision"],
        "answerability_recall": board["answerability"]["recall"],
        "cost_total": board["cost"]["total"],
        "cost_per_question": board["cost"]["per_question"],
        "tokens_per_question": {"input": per_case("input_tokens"), "cached": per_case("cached_input_tokens"),
                                "output": per_case("output_tokens")},
        "latency_p50": board["latency"]["question"]["p50"],
        "latency_p95": board["latency"]["question"]["p95"],
        "determinism": (board["determinism"] or {}).get("share"),
        "faithfulness": (board.get("faithfulness") or {}).get("rate"),
    }


def _retrieval_metrics(board: Dict[str, Any]) -> Dict[str, Any]:
    """The recall summary of a mode retrieval board, as 0.6's retrieval records held it."""
    summary = board.get("retrieval") or {}
    return {"questions": summary.get("cases", 0),
            **{k: summary.get(k) for k in ("table_recall", "column_recall", "perfect_tables", "perfect_columns",
                                           "tables_sent", "columns_sent")}}


_RESULT_FIELDS = ("id", "role", "pass", "status", "lenient_status", "cost")
_SCORE_DETAIL = ("lenient", "error")  # what execution_passed and case_status read


def _slim_scores(scores: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for name, score in (scores or {}).items():
        slim = {"passed": score.get("passed"), "value": score.get("value")}
        detail = {k: v for k, v in (score.get("detail") or {}).items() if k in _SCORE_DETAIL}
        if detail:
            slim["detail"] = detail
        out[name] = slim
    return out


def slim_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """One case run as a record keeps it: its verdicts and cost, enough to gate on and to render the pages."""
    slim = {k: result[k] for k in _RESULT_FIELDS if k in result}
    slim["scores"] = _slim_scores(result.get("scores"))
    generated = result.get("generated")
    slim["generated"] = None if not generated else {
        "status": generated.get("status"), "lenient_status": generated.get("lenient_status"),
        "scores": _slim_scores(generated.get("scores"))}
    return slim


def make_record(report: Any, name: str, *, recorded_at: dt.datetime, engine_version: str,
                code: Dict[str, Any], note: Optional[str] = None) -> Dict[str, Any]:
    """The record for config ``name`` of an eval report (an ``EvalReport`` or its JSON)."""
    from nl2sql.evaluation.report import suite_identity

    rep = _report_dict(report)
    mode = rep["mode"]
    board = rep["configs"][name]
    passes = rep.get("passes", 1)
    record = {
        "schema": SCHEMA_VERSION, "kind": mode, "recorded_at": _stamp(recorded_at),
        "engine_version": engine_version, "git_commit": code.get("git_commit") or UNKNOWN,
        "git_dirty": code.get("git_dirty", UNKNOWN), "note": note or None,
        "dataset": suite_identity(rep), "database": rep.get("database") or dict(UNKNOWN_DATABASE),
        "config": {"name": name, **({"models": board["models"]} if board.get("models") else {})},
        "roles": rep.get("roles"),
        "passes": passes,
        # Whether verified queries could answer; a record without the field ran without them.
        "curated": bool(rep.get("curated", False)),
        "metrics": _retrieval_metrics(board) if mode == "retrieval" else _execution_metrics(board, passes),
        "stopped": rep.get("stopped"),
        "partial": bool(rep.get("stopped")) or board.get("completed_cases", 0) < board.get("planned_cases", 0),
        "scoreboard": {**board, "results": [slim_result(r) for r in board.get("results") or []]},
    }
    if mode == "retrieval":
        record["settings"] = (rep.get("settings") or {}).get("retrieval") or {}
    return record


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "config"


def record_relpath(record: Dict[str, Any]) -> pathlib.Path:
    """Where a record lives under ``benchmarks/``, without a clash suffix."""
    sha = record.get("git_commit") or UNKNOWN
    stem = f"{record['recorded_at'][:10]}_{sha[:7] if sha != UNKNOWN else UNKNOWN}_{_slug(record['config']['name'])}"
    return pathlib.Path(record["kind"]) / _slug(record["database"]["datasource_id"]) / f"{stem}.json"


def _dump(record: Dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str) + "\n"


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


def write_records(report: Any, benchmarks_dir: pathlib.Path = BENCHMARKS_DIR, *,
                  recorded_at: Optional[dt.datetime] = None, engine_version: Optional[str] = None,
                  code: Optional[Dict[str, Any]] = None, note: Optional[str] = None) -> List[pathlib.Path]:
    """Writes one record per config of ``report`` (an ``EvalReport`` or its JSON); returns the paths written."""
    recorded_at = recorded_at or dt.datetime.now(dt.timezone.utc)
    version = engine_version or installed_engine_version()
    code = code if code is not None else code_version()
    rep = _report_dict(report)
    return [_write_new(benchmarks_dir, make_record(rep, name, recorded_at=recorded_at, engine_version=version,
                                                   code=code, note=note))
            for name in rep["configs"]]


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
    """The runs a record is compared with over time: same mode, database and config."""
    return mode_of(r), r["database"]["datasource_id"], r["config"]["name"]


def series_key(r: Dict[str, Any]) -> Tuple:
    """What must match for two runs to be comparable: mode, suite, schema, and for mode full the roles and curated answers."""
    full = mode_of(r) == "full"
    roles = tuple(r.get("roles") or []) if full else ()
    curated = bool(r.get("curated", False)) if full else False
    return mode_of(r), r["dataset"]["sha256"], r["database"]["schema_fingerprint"], roles, curated


def comparable(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return series_key(a) == series_key(b)


def previous_runs(recs: Sequence[Dict[str, Any]]) -> Dict[int, Optional[Dict[str, Any]]]:
    """For each record (by ``id``), the run just before it on the same mode, database and config."""
    previous: Dict[int, Optional[Dict[str, Any]]] = {}
    last: Dict[Tuple, Dict[str, Any]] = {}
    for r in reversed(_newest_first(recs)):
        previous[id(r)] = last.get(line_key(r))
        last[line_key(r)] = r
    return previous


def deltas(new: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """The change in each headline metric, ``new`` minus ``old``; None where either is missing."""
    out = {}
    for field, _, _ in HEADLINE[mode_of(new)]:
        a, b = new["metrics"].get(field), old["metrics"].get(field)
        out[field] = None if a is None or b is None else round(a - b, 6)
    return out


def _series_break(new: Dict[str, Any], old: Dict[str, Any]) -> str:
    changed = [label for label, same in (
        ("suite", new["dataset"]["sha256"] == old["dataset"]["sha256"]),
        ("schema", new["database"]["schema_fingerprint"] == old["database"]["schema_fingerprint"]),
        ("roles", series_key(new)[3] == series_key(old)[3]),
        ("curated answers", series_key(new)[4] == series_key(old)[4])) if not same]
    return f"new series ({', '.join(changed)} changed)"


def flips(new: Dict[str, Any], old: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The questions that flipped between two records, and McNemar's p-value.

    Read from each record's committed scoreboard, so the history page can say
    how many questions actually moved rather than only how the headline did.
    A case passes on execution accuracy, the headline (mode retrieval: on its
    status). None for a record with no per-question results (0.6's retrieval
    run, or one written before they were kept).
    """
    from nl2sql.evaluation.gate import question_flips
    from nl2sql.evaluation.report import execution_passed

    now = (new.get("scoreboard") or {}).get("results")
    before = (old.get("scoreboard") or {}).get("results")
    if not now or not before:
        return None
    if mode_of(new) == "retrieval":
        return question_flips(now, before, passed=lambda r: r["status"] == "pass")
    return question_flips(now, before, passed=execution_passed)


def describe_change(new: Dict[str, Any], old: Optional[Dict[str, Any]], *, show_flips: bool = False) -> str:
    """The Δ cell: the change against the previous run, or why there is none.

    With ``show_flips`` the headline deltas are followed by how many questions
    flipped each way and McNemar's exact p-value on them, which is what says
    whether a difference of a few points means anything at n = 47.
    """
    if old is None:
        return "first run"
    if not comparable(new, old):
        return _series_break(new, old)
    parts = []
    for field, label, unit in HEADLINE[mode_of(new)]:
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
    pass_k = (r["metrics"].get("pass_k") or {}) if mode_of(r) != "retrieval" else {}
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
    """The newest record for each (mode, database, config), in that order."""
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in _newest_first(recs):
        latest.setdefault(line_key(r), r)
    order = list(KIND_TITLES)
    return [latest[k] for k in sorted(latest, key=lambda k: (order.index(k[0]) if k[0] in order else 99, k))]


def _groups(recs: Sequence[Dict[str, Any]]) -> List[Tuple[Tuple[str, str], List[Dict[str, Any]]]]:
    """Records grouped by (mode, database), mode full first, each group newest first."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in _newest_first(recs):
        groups.setdefault((mode_of(r), r["database"]["datasource_id"]), []).append(r)
    order = list(KIND_TITLES)
    return sorted(groups.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 99, kv[0]))


def _group_heading(mode: str, group: List[Dict[str, Any]]) -> str:
    db = group[0]["database"]
    size = "" if db.get("tables") is None else f", {db['tables']} tables / {db['columns']} columns"
    return f"## {KIND_TITLES.get(mode, mode)}: {db['datasource_id']} ({db['engine']}{size})"


def _full_section(group: List[Dict[str, Any]], previous) -> str:
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
        [[_when(r), _commit(r), r.get("note") or "-", r["config"]["name"], _models(r["config"].get("models") or {}),
          _dataset(r), _schema(r), ", ".join(r["roles"] or []), _passes(r),
          _ci(r["metrics"]["accuracy"], r["metrics"].get("accuracy_interval")),
          _ci(r["metrics"].get("lenient_accuracy"), r["metrics"].get("lenient_accuracy_interval")),
          _pct(r["metrics"].get("faithfulness")), _usd(r["metrics"]["cost_per_question"]),
          describe_change(r, previous[id(r)], show_flips=True),
          f"{_pct(r['metrics']['answerability_precision'])} / {_pct(r['metrics']['answerability_recall'])}",
          _tokens(r["metrics"]["tokens_per_question"]), _sec(r["metrics"]["latency_p50"]),
          _sec(r["metrics"]["latency_p95"]), _pct(r["metrics"]["determinism"]), _status(r)] for r in group])
    return ("`nl2sql eval run --mode full`: the configured model end to end. Accuracy is execution\n"
            "accuracy (`result_match` and `outcome`), the measure 0.6's tier 2 published, so the runs\n"
            "before and after 0.7 read the same; faithfulness is its own column.\n\n"
            f"### Latest per config\n\n{latest}\n\n### All runs\n\n{runs}\n")


def _plans_section(group: List[Dict[str, Any]], previous) -> str:
    runs = _table(
        ["Date (UTC)", "Commit", "Note", "Dataset", "Schema", "Roles", "Cases", "Accuracy (execution, 95% CI)",
         "Δ vs previous", "Status"],
        [[_when(r), _commit(r), r.get("note") or "-", _dataset(r), _schema(r), ", ".join(r["roles"] or ["all"]),
          r["metrics"]["cases"], _ci(r["metrics"]["accuracy"], r["metrics"].get("accuracy_interval")),
          describe_change(r, previous[id(r)], show_flips=True), _status(r)] for r in group])
    return ("`nl2sql eval run --mode plans`: each case's hand-written plan through the validator,\n"
            "generator and executor, on a fake LLM, once per role. No key; every case should pass.\n"
            "CI's integration job gates on the newest run here.\n\n"
            f"{runs}\n")


def _retrieval_section(group: List[Dict[str, Any]], previous) -> str:
    runs = _table(
        ["Date (UTC)", "Commit", "Note", "Dataset", "Schema", "Search", "Embedding", "Cases scored", "Table recall",
         "Column recall", "Δ vs previous", "Tables / columns sent"],
        [[_when(r), _commit(r), r.get("note") or "-", _dataset(r), _schema(r),
          f"k {r['settings']['table_k']} tables / {r['settings']['planning_k']} planning",
          r["settings"].get("embedding", "-"), r["metrics"]["questions"], _pct(r["metrics"]["table_recall"]),
          _pct(r["metrics"]["column_recall"]), describe_change(r, previous[id(r)]),
          f"{r['metrics']['tables_sent']:.1f} / {r['metrics']['columns_sent']:.1f}"] for r in group])
    return ("`nl2sql eval run --mode retrieval`: the share of each answerable case's needed tables and\n"
            "columns that schema retrieval sends the planner, with the vector search forced on and no\n"
            "LLM. Mean over cases; tables and columns sent are means too. CI's integration job gates on\n"
            "the newest run here.\n\n"
            f"{runs}\n")


def render_history(recs: Sequence[Dict[str, Any]]) -> str:
    """``docs/benchmarks.md``: per mode and database, the latest runs and every run newest first."""
    head = ("# Benchmark Results\n\n"
            "Recorded eval runs of the engine on the Chinook suite. Generated by `nl2sql eval publish`\n"
            "from the records in `benchmarks/`; do not edit by hand. Grouped by mode and database,\n"
            "newest first; the runs 0.6's tier 2 recorded are mode full runs. Accuracy is execution\n"
            "accuracy (`result_match` and `outcome`, the measure 0.6 published), with its 95% Wilson\n"
            "interval. \"Δ vs previous\" is the change against the previous run of the same config, and\n"
            "how many cases flipped each way with McNemar's exact p-value on them: with a suite this\n"
            "size a few points either way is noise, so read the flips and the p-value, not the headline.\n"
            "Runs on another suite (`@digest`), another schema or, for mode full, other roles or curated\n"
            "answers on are not comparable and start a new series. See\n"
            "[the Chinook suite](testing/evaluation-dataset.md).\n")
    if not recs:
        return f"{head}\n{EMPTY}\n"
    previous = previous_runs(recs)
    sections = []
    render = {"full": _full_section, "plans": _plans_section, "retrieval": _retrieval_section}
    for (mode, _), group in _groups(recs):
        sections.append(f"{_group_heading(mode, group)}\n\n{render[mode](group, previous)}")
    return head + "\n" + "\n".join(sections)


def render_readme_block(recs: Sequence[Dict[str, Any]]) -> str:
    """The README block: mode full accuracy then retrieval recall, latest run per database and config.

    Each mode gets its own heading and table, and says so when it has no run yet.
    Mode plans is CI's gate, not a headline: it is on the history page only.
    """
    previous = previous_runs(recs) if recs else {}
    latest = _latest_per_line(recs)
    full = [r for r in latest if mode_of(r) == "full"]
    retrieval = [r for r in latest if mode_of(r) == "retrieval"]
    full_body = _table(
        ["Config", "Database", "Date (UTC)", "Commit", "Accuracy (strict)", "Accuracy (lenient)",
         "Faithfulness", "$/question", "Δ vs previous"],
        [[r["config"]["name"], r["database"]["datasource_id"], r["recorded_at"][:10], _commit(r),
          _ci(r["metrics"]["accuracy"], r["metrics"].get("accuracy_interval")),
          _pct(r["metrics"].get("lenient_accuracy")),
          _pct(r["metrics"].get("faithfulness")),
          _usd(r["metrics"]["cost_per_question"]), describe_change(r, previous[id(r)])] for r in full],
    ) if full else FULL_EMPTY
    retrieval_body = _table(
        ["Database", "Date (UTC)", "Commit", "Table recall", "Column recall", "Δ vs previous"],
        [[r["database"]["datasource_id"], r["recorded_at"][:10], _commit(r), _pct(r["metrics"]["table_recall"]),
          _pct(r["metrics"]["column_recall"]), describe_change(r, previous[id(r)])] for r in retrieval],
    ) if retrieval else RETRIEVAL_EMPTY
    parts = [README_FULL_HEADING, README_FULL_INTRO, full_body,
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
