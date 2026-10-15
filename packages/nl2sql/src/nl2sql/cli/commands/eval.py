"""``nl2sql eval``: run eval suites, gate a run against a baseline, regenerate expected rows, seed drafts, publish.

* ``run``        -- ``engine.evals.run`` in one mode, then ``engine.evals.gate``
  with ``--baseline``. Writes the report (and when and which code ran it) to
  ``--out``, by default ``eval_<mode>.json`` next to ``nl2sql.yaml``.
* ``regenerate`` -- ``engine.evals.regenerate``: a suite's expected rows, through the adapter.
* ``seed``       -- ``engine.evals.seed``: draft cases from a datasource, as the
  caller ``--role`` names (else ``policies.default_role``; with neither it
  refuses). Exits 2 on a refusal, 3 when the cost cap stopped it early.
* ``publish``    -- each report given becomes one record per config under
  ``benchmarks/``; then ``docs/benchmarks.md`` and the README block are
  rebuilt from every record. Run from the repo root.

``run`` exits 0 when it ran and nothing regressed, 1 on a regression against
``--baseline``, 2 on a usage error (a bad ``--llm``, a run the engine refuses,
mode full with ``CI`` set, a baseline that is missing or unreadable, or one
that cannot be compared) and 3 when the cost cap stopped mode full early.

``CI`` counts as set whatever its value, ``CI=false`` included: refusing a
paid run by mistake is cheap, spending a key in CI by mistake is not. Mode
full and ``seed`` both refuse to run with it set.
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Dict, List, Optional

import click
import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console
from nl2sql.evaluation import presets
from nl2sql.evaluation.records import BENCHMARKS_DIR, HISTORY_PATH, README_PATH, import_records, publish, \
    run_stamp, write_records
from nl2sql.llm.models import LLMFileConfig

app = typer.Typer(help="Evals: run suites and gate them against a baseline, regenerate expected rows, "
                       "publish recorded runs.", no_args_is_help=True)

EXIT_REGRESSION = 1
EXIT_USAGE = 2
EXIT_STOPPED_AT_MAX_COST = 3


def _usage(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {escape(message)}", soft_wrap=True)
    raise typer.Exit(EXIT_USAGE)


def _llm_file_or_preset(value: str) -> tuple:
    """``(name, config)`` for a preset name or an ``llm`` section file."""
    path = pathlib.Path(value)
    if value.lower().endswith((".yaml", ".yml")) or path.is_file():
        return path.stem, presets.load_llm_file(path)
    name, preset = presets.resolve_llm_spec(value)
    return name, presets.load_llm_file(preset)


def llm_configs(specs: List[str]) -> Dict[str, LLMFileConfig]:
    """The configs ``--llm`` names, in order: ``PRESET``, ``FILE.yaml``, ``NAME=PRESET`` or ``NAME=FILE``.

    Raises:
        ValueError: an unknown preset, a missing file, or two configs with one name.
    """
    configs: Dict[str, LLMFileConfig] = {}
    for spec in specs:
        name, sep, value = spec.strip().partition("=")
        if not sep:
            name, value = "", name
        if sep and (not name.strip() or not value.strip()):
            raise ValueError(f"--llm expects PRESET, FILE.yaml, NAME=PRESET or NAME=FILE, got '{spec}'.")
        default_name, cfg = _llm_file_or_preset(value.strip())
        name = name.strip() or default_name
        if name in configs:
            raise ValueError(f"Two configs are named '{name}'; give one a NAME= prefix.")
        configs[name] = cfg
    return configs


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.1%}"


def _print_report(report) -> None:
    for name, board in report.configs.items():
        total = board["summary"]["total"]
        accuracy = board["accuracy"]
        line = (f"[{name}] {board['completed_cases']} case runs: {total['pass']} pass, {total['fail']} fail, "
                f"{total['error']} error, {total['skip']} skip; ")
        if report.mode == "retrieval":
            line += f"passed {_pct(accuracy['overall'])}"
        else:
            line += f"execution accuracy {_pct(accuracy['execution'])}, all scorers {_pct(accuracy['overall'])}"
            if report.curated:
                line += f", generated {_pct(accuracy['generated'])}"
        console.print(escape(line), soft_wrap=True)
        if board.get("retrieval"):
            r = board["retrieval"]
            console.print(escape(f"[{name}] table recall {_pct(r['table_recall'])}, column recall "
                                 f"{_pct(r['column_recall'])}"), soft_wrap=True)
    if report.mode == "full":
        console.print(escape(f"Spent ${report.spent:.4f}" + (f" of ${report.max_cost_usd:.2f}"
                                                             if report.max_cost_usd else "")), soft_wrap=True)


def _print_gate(result) -> None:
    for name, measures in result.compared.items():
        for measure, diff in measures.items():
            label = name if measure == "accuracy" else f"{name} ({measure})"
            console.print(escape(
                f"Against the baseline, {label}: {_pct(diff['baseline_accuracy'])} -> {_pct(diff['accuracy'])}, "
                f"{len(diff['pass_to_fail'])} flipped to fail, {len(diff['fail_to_pass'])} to pass "
                f"(McNemar p={diff['p_value']:.3f})"), soft_wrap=True)


@app.command("run")
def run_command(
    ctx: typer.Context,
    suite: Annotated[Optional[List[pathlib.Path]], typer.Option(
        "--suite", help="A suite file to run (repeatable). Without it, the suites evals.yaml names.")] = None,
    mode: Annotated[str, typer.Option(
        "--mode", click_type=click.Choice(["full", "plans", "retrieval"]),
        help="full: the configured model end to end, under a cost cap; plans: each case's gold plan on a "
             "fake LLM (no key); retrieval: routing and schema retrieval only (no model).")] = "full",
    llm: Annotated[Optional[List[str]], typer.Option(
        "--llm", help="Mode full: an LLM config to compare (repeatable): a preset name (gpt-5.4, mini-helpers, "
                      "claude-planner), a FILE.yaml holding an llm section, or NAME=PRESET / NAME=FILE. "
                      "Without it, the config's own llm section runs as 'default'.")] = None,
    cases: Annotated[Optional[List[str]], typer.Option(
        "--cases", help="Only these case ids or tags (repeatable).")] = None,
    passes: Annotated[int, typer.Option(
        "--passes", min=1, help="Run every case this many times; the board reports pass^N and determinism.")] = 1,
    max_cost: Annotated[Optional[float], typer.Option(
        "--max-cost", help="Mode full: stop before a case that could take spend past this many USD "
                           "(default: evals.yaml's max_cost_usd).")] = None,
    baseline: Annotated[Optional[pathlib.Path], typer.Option(
        "--baseline", help="A report (eval run --out) or record to gate against, or a benchmarks/ folder, "
                           "whose newest record of this mode, database and config is used. Exits 1 on a "
                           "regression.")] = None,
    out: Annotated[Optional[pathlib.Path], typer.Option(
        "--out", help="Where to write the report (default: eval_<mode>.json next to nl2sql.yaml).")] = None,
) -> None:
    """
    Run the eval suites in one mode and, with --baseline, gate the run against it.

    The gate compares execution accuracy (result_match and outcome, the measure
    0.6's tier 2 published), as users see it and on the generated path, and
    cost per case. Exits 0 when nothing regressed, 1 on a regression, 2 on a
    usage error or a baseline of another suite or mode, 3 when the cost cap
    stopped mode full early.
    """
    if mode == "full" and os.environ.get("CI"):
        _usage("Mode full calls a paid model and never runs in CI (CI is set). Use --mode plans or retrieval.")
    try:
        llms = llm_configs(llm or [])
    except ValueError as error:
        _usage(str(error))

    def on_case(name: str, record: dict, spent: float) -> None:
        console.print(escape(f"[{name}] pass {record['pass']} {record['id']}/{record['role']} "
                             f"{record['status'].upper():5} ${record['cost']:.4f}  spent ${spent:.4f}"),
                      soft_wrap=True)

    with build_engine(ctx) as engine:
        try:
            report = engine.evals.run(suites=list(suite) if suite else None, mode=mode, llm=llms or None,
                                      cases=list(cases) if cases else None, passes=passes, max_cost_usd=max_cost,
                                      on_case=on_case)
        except ValueError as error:
            _usage(str(error))
        _print_report(report)
        folder = engine.config.folder or pathlib.Path.cwd()
        target = out or pathlib.Path(folder) / f"eval_{mode}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({**report.model_dump(mode="json"), "run": run_stamp()}, indent=2,
                                     ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        console.print(f"Report: {escape(str(target))}", soft_wrap=True)
        result = None
        if baseline is not None:
            try:
                result = engine.evals.gate(report, baseline)
            except (OSError, ValueError, KeyError, TypeError) as error:
                _usage(f"Cannot read the baseline {baseline}: {type(error).__name__}: {error}")

    if result is not None:
        _print_gate(result)
        for problem in result.regressions:
            console.print(f"[bold red]Regression:[/bold red] {escape(problem)}", soft_wrap=True)
        if result.series_break:
            console.print(f"[bold red]Not compared:[/bold red] {escape(result.series_break)}", soft_wrap=True)
            raise typer.Exit(EXIT_USAGE)
        if result.regressions:
            raise typer.Exit(EXIT_REGRESSION)
        console.print(f"[green]No regression against {escape(str(baseline))}.[/green]", soft_wrap=True)
    if report.stopped:
        console.print(f"[yellow]Stopped early ({escape(report.stopped)}): the report is partial.[/yellow]",
                      soft_wrap=True)
        raise typer.Exit(EXIT_STOPPED_AT_MAX_COST)


@app.command("regenerate")
def regenerate_command(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="The suite file, relative to nl2sql.yaml or absolute.")],
) -> None:
    """
    Write a suite's expected rows: each approved case's sql and alt_sql through its datasource's adapter.

    A case whose SQL fails is listed and left unchanged, and the command exits 1.
    """
    with build_engine(ctx) as engine:
        result = engine.evals.regenerate(path)
    console.print(escape(f"Wrote {result.written} result set(s) to {result.path}."), soft_wrap=True)
    for failure in result.failed:
        console.print(f"[bold red]Unchanged:[/bold red] {escape(failure['case'])}: {escape(failure['error'])}",
                      soft_wrap=True)
    if result.failed:
        raise typer.Exit(1)


@app.command("seed")
def seed_command(
    ctx: typer.Context,
    datasource: Annotated[str, typer.Option("--datasource", help="The datasource to seed drafts from.")],
    out: Annotated[str, typer.Option(
        "--out", help="The suite file to append drafts to, relative to nl2sql.yaml or absolute. Never a suite "
                      "evals.yaml names or a file holding approved cases.")],
    count: Annotated[int, typer.Option("--count", min=1, help="How many drafts to write at most.")] = 20,
    role: Annotated[Optional[str], typer.Option(
        "--role", help="The role to seed as: only the tables it may read are used. Without it, "
                       "policies.default_role.")] = None,
    max_cost: Annotated[Optional[float], typer.Option(
        "--max-cost", help="Stop seeding's model calls at this many USD (default: evals.yaml's max_cost_usd).")]
    = None,
) -> None:
    """
    Draft eval cases from a datasource's schema, for a person to review.

    The model writes questions with typed plans, five per call; each plan is
    validated and rendered by the engine and run read-only, and the survivors
    are appended as status: draft. Review each, set status: approved, then run
    `nl2sql eval regenerate`. Exits 2 on a refusal, 3 when the cost cap stopped it.
    """
    from nl2sql import UserContext

    if os.environ.get("CI"):
        _usage("Seeding calls a paid model and never runs in CI (CI is set).")
    with build_engine(ctx) as engine:
        role = role or engine.config.policies.default_role
        if not role:
            _usage("Seeding asks as a role: pass --role <role>, or set policies.default_role in nl2sql.yaml.")
        try:
            report = engine.evals.seed(datasource, count=count, out=out, user_context=UserContext(roles=[role]),
                                       max_cost_usd=max_cost)
        except (ValueError, PermissionError) as error:
            _usage(str(error))
    console.print(escape(
        f"Wrote {report.written} draft case(s) to {report.path}; dropped {report.dropped_invalid} invalid, "
        f"{report.dropped_failed} failed, {report.dropped_empty} empty, {report.dropped_duplicate} duplicate."),
        soft_wrap=True)
    cost = report.usage.total.cost
    console.print(escape(f"{report.usage.total.calls} model call(s)"
                         + (f", ${cost:.4f}" if cost is not None else "")), soft_wrap=True)
    if report.stopped_at_cost_cap:
        console.print("[yellow]Stopped at the cost cap: fewer drafts than asked for.[/yellow]", soft_wrap=True)
        raise typer.Exit(EXIT_STOPPED_AT_MAX_COST)


@app.command("publish")
def publish_command(
    reports: Annotated[Optional[List[pathlib.Path]], typer.Argument(
        help="Reports `nl2sql eval run` wrote (--out) to record: one record per config.")] = None,
    note: Annotated[Optional[str], typer.Option(
        "--note", help="A short label for what changed in these runs, e.g. \"slim prompts\"; kept in each record.",
    )] = None,
    sources: Annotated[Optional[List[pathlib.Path]], typer.Option(
        "--from", help="Another benchmarks/ folder, or a project folder holding one (repeatable): copy its "
                       "records not already here first. A record here with other content is never overwritten.",
    )] = None,
    results_dir: Annotated[pathlib.Path, typer.Option(
        "--results-dir", help="The benchmarks folder records are written to and read from.")] = BENCHMARKS_DIR,
    history_path: Annotated[pathlib.Path, typer.Option(
        "--history-path", help="The history page to write.")] = HISTORY_PATH,
    readme_path: Annotated[pathlib.Path, typer.Option(
        "--readme-path", help="The README whose BENCHMARKS block is replaced.")] = README_PATH,
) -> None:
    """
    Record eval reports under benchmarks/, then rebuild docs/benchmarks.md and the README results block.

    Run from the repo root. Each record keeps the time and commit of the run
    that made the report. No key, no network; the same records always give
    byte-identical pages. Exits 1 on a conflict with a record already here.
    """
    import datetime as dt

    for path in reports or []:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = data.get("run") or run_stamp()
        recorded_at = dt.datetime.strptime(stamp["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
        for written in write_records(data, results_dir, recorded_at=recorded_at,
                                     engine_version=stamp["engine_version"],
                                     code={"git_commit": stamp["git_commit"], "git_dirty": stamp["git_dirty"]},
                                     note=note):
            console.print(f"Record: {escape(str(written))}", soft_wrap=True)
    conflicts: List[str] = []
    if sources:
        outcome = import_records(sources, results_dir)
        for rel in outcome["copied"]:
            console.print(f"Copied {escape(rel)}", soft_wrap=True)
        for old in outcome["old_format"]:
            console.print(f"[yellow]Skipped {escape(old)}: written before the current record layout.[/yellow]",
                          soft_wrap=True)
        conflicts = outcome["conflicts"]
        for rel in conflicts:
            console.print(f"[bold red]Conflict:[/bold red] {escape(rel)} is already here with other content; "
                          "kept the one here.", soft_wrap=True)
    count = publish(results_dir, history_path, readme_path)
    console.print(f"[green]Published {count} record(s) from {escape(str(results_dir))} to "
                  f"{escape(str(history_path))} and {escape(str(readme_path))}.[/green]", soft_wrap=True)
    if conflicts:
        raise typer.Exit(1)
