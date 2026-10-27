import json
import sys
from typing import Dict, List, Optional

from nl2sql.cli.reporting import ConsolePresenter
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql import IndexProgress


@handle_cli_errors
def run_indexing(
    engine,
    datasource_ids: Optional[List[str]] = None,
    refresh: bool = False,
) -> None:
    """
    Rebuilds the schema index, one datasource at a time.

    Each datasource's new entries are written beside its current ones, in
    the index of the configured embedding model, and its build pointer is
    switched to them only when all are written (see
    ``nl2sql.indexing.rebuild``): a failure leaves that datasource's previous
    entries answering questions, and no datasource's rebuild touches
    another's entries.

    Args:
        engine: The ``NL2SQL`` engine, built from the config and checked.
        datasource_ids: Only these datasources; every configured one when None.
        refresh: Write the entries again from the stored snapshot, the
            ``curated`` section and the knowledge; reads no schema and calls
            no model.

    Raises:
        SystemExit: With code 1 if any datasource failed to index.
    """
    presenter = ConsolePresenter()
    presenter.print_info(f"Indexing into {engine.config.storage.path}")

    tasks: Dict[str, object] = {}

    def on_datasource(progress: IndexProgress) -> None:
        ds_id = progress.datasource_id
        if progress.state == "started":
            tasks[ds_id] = presenter.start_task_line(f"Indexing {ds_id}...")
            return
        task = tasks.pop(ds_id, None)
        if progress.state == "done":
            if task is not None:
                presenter.finish_task_line(task, f"{ds_id} indexed", success=True)
        else:
            if task is not None:
                presenter.finish_task_line(task, f"{ds_id} failed", success=False)
            presenter.print_error(f"Failed to index {ds_id}: {progress.reason}")

    build = engine.index.refresh if refresh else engine.index.build
    result = build(datasource_ids, on_datasource=on_datasource)
    total_adapters = len(result.stats) + len(result.empty) + len(result.errors)

    if result.errors:
        presenter.print_table(result.errors, "Indexing Errors", columns=["datasource_id", "error"])

    if result.stats:
        summary_rows = []
        total_chunks = 0
        totals_by_type: Dict[str, int] = {}

        for s in result.stats:
            ds_id = s.get("datasource_id", "unknown")
            schema_version = s.get("schema_version", "-")
            chunk_stats = {
                k: v for k, v in s.items()
                if k not in ("datasource_id", "schema_version", "issues")
            }
            issues = s.get("issues") or []
            if issues:
                presenter.print_warning(f"{ds_id}: indexed with {len(issues)} issue(s)")
                presenter.print_table(issues, f"Indexing issues: {ds_id}",
                                      columns=["table", "column", "stage", "message"])
            ds_total = sum(v for v in chunk_stats.values() if isinstance(v, int))
            total_chunks += ds_total
            for key, val in chunk_stats.items():
                if isinstance(val, int):
                    totals_by_type[key] = totals_by_type.get(key, 0) + val

            summary_rows.append(
                {
                    "datasource_id": ds_id,
                    "schema_version": schema_version,
                    "total_chunks": ds_total,
                    "chunks": json.dumps(chunk_stats, separators=(",", ":")),
                }
            )

        presenter.print_table(
            summary_rows,
            "Indexing Summary",
            columns=["datasource_id", "schema_version", "total_chunks", "chunks"],
        )

        if totals_by_type:
            totals_str = ", ".join([f"{k}={v}" for k, v in sorted(totals_by_type.items())])
            presenter.print_info(f"Total chunks indexed: {total_chunks} ({totals_str})")

    presenter.print_info(
        f"Datasources: total={total_adapters}, succeeded={len(result.stats)}, "
        f"failed={len(result.errors)}, empty={len(result.empty)}"
    )

    if result.empty:
        presenter.print_warning(f"Datasources with empty stats: {', '.join(result.empty)}")

    if total_adapters == 0:
        presenter.print_warning(
            "No datasources are configured, so there is nothing to index. "
            "The previous index is unchanged."
        )
        sys.exit(1)

    if not result.ok:
        # Any failure is fatal, not just a total one: scripts chaining
        # `nl2sql index && nl2sql run ...` need that signal.
        presenter.print_warning(
            "Indexing completed with errors. Each failed datasource keeps its previous index entries unchanged."
        )
        sys.exit(1)

    presenter.print_success("Indexing complete.")


@handle_cli_errors
def run_prune(engine) -> None:
    """Drops the index namespaces of every embedding model but the configured one, and says which."""
    presenter = ConsolePresenter()
    dropped = engine.index.prune()
    if not dropped:
        presenter.print_info("Nothing to prune: only the current embedding model's index exists.")
        return
    for name in dropped:
        presenter.print_info(f"Dropped {name}")
    presenter.print_success(f"Pruned {len(dropped)} index namespace(s); kept the current one.")


def _change_lines(diff) -> List[str]:
    """One line per change in a ``SchemaDiff``, for the terminal."""
    def table(ref) -> str:
        return f"{ref.schema_name}.{ref.table_name}"

    lines = [f"+ table {table(t)}" for t in diff.tables_added]
    lines += [f"- table {table(t)}" for t in diff.tables_dropped]
    lines += [f"+ column {table(c.table)}.{c.column_name}" for c in diff.columns_added]
    lines += [f"- column {table(c.table)}.{c.column_name}" for c in diff.columns_dropped]
    lines += [f"~ column {table(c.table)}.{c.column}: "
              + ", ".join(f"{key} {old} -> {new}" for key, (old, new) in c.changes.items())
              for c in diff.columns_changed]
    lines += [f"+ foreign key {table(f.table)}({', '.join(f.columns)}) -> "
              f"{table(f.referred_table)}({', '.join(f.referred_columns)})" for f in diff.foreign_keys_added]
    lines += [f"- foreign key {table(f.table)}({', '.join(f.columns)}) -> "
              f"{table(f.referred_table)}({', '.join(f.referred_columns)})" for f in diff.foreign_keys_dropped]
    if diff.engine_type:
        lines.append(f"~ engine type {diff.engine_type[0]} -> {diff.engine_type[1]}")
    return lines


def run_drift_check(engine, datasource_ids: Optional[List[str]], roles: List[str], as_json: bool) -> int:
    """``nl2sql index --check``: prints each datasource's drift report; returns the exit code.

    0 when nothing drifted, 1 when something did, 2 when a check failed (a
    failure wins) or nothing was checked (no datasource, or the roles reach
    none). Never re-indexes. A datasource never indexed is reported and does
    not change the code.
    """
    from nl2sql import UserContext

    presenter = ConsolePresenter()
    try:
        reports = engine.index.check(datasource_ids, user_context=UserContext(roles=roles))
    except ValueError as error:
        presenter.print_error(str(error))
        return 2
    if as_json:
        import typer

        typer.echo(json.dumps([r.model_dump(mode="json") for r in reports], indent=2))
    else:
        for report in reports:
            version = f" (build {report.live_version})" if report.live_version else ""
            if report.status == "unchanged":
                presenter.print_success(f"{report.datasource_id}: unchanged{version}")
            elif report.status == "not_indexed":
                presenter.print_warning(f"{report.datasource_id}: not indexed; run `nl2sql index` first")
            elif report.status == "failed":
                presenter.print_error(f"{report.datasource_id}: check failed: {report.error}")
            else:
                presenter.print_warning(f"{report.datasource_id}: drifted from its build{version}")
                for line in _change_lines(report.diff):
                    presenter.console.print(f"    {line}", markup=False, soft_wrap=True)
                impact = report.impact
                for query in impact.verified_queries:
                    presenter.console.print(f"    verified query {query.query_id}: {'; '.join(query.reasons)}",
                                            markup=False, soft_wrap=True)
                for rule in impact.policy_rules:
                    presenter.console.print(f"    role {rule.role}: {rule.kind} '{rule.pattern}' now matches "
                                            "nothing", markup=False, soft_wrap=True)
                for name in impact.preferred_tables:
                    presenter.console.print(f"    preferred table {name} was dropped", markup=False, soft_wrap=True)
    if not reports:
        presenter.print_error("Nothing was checked: no datasource is configured, or the role(s) "
                              f"{', '.join(roles)} reach none. Pass --role with a role that reads them.")
        return 2
    if any(r.status == "failed" for r in reports):
        return 2
    return 1 if any(r.status == "drifted" for r in reports) else 0
