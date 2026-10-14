"""``nl2sql run``: one question through the engine, shown as it runs from ``stream_query``'s events."""
import json
import sys
import time
from typing import List, Optional, Tuple

from rich.text import Text

from nl2sql import QueryResult, UserContext
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.reporting import ConsolePresenter
from nl2sql.cli.types import RunConfig


@handle_cli_errors
def run_pipeline(
    config: RunConfig,
    engine,
) -> None:
    """Runs one question through ``engine`` and prints what it did.

    The live status follows the run's ``node_started`` events; ``--show-perf``
    is the time of each step from its ``node_finished`` event. Exits 1 when the
    run could not finish, or finished with a blocking error.
    """
    if not config.query:
        return

    presenter = ConsolePresenter()
    presenter.print_info(f"Query: {config.query}")
    if config.no_exec:
        presenter.print_warning("Execution disabled (--no-exec): plan, validation and SQL only.")

    record_step = _step_metrics(engine)
    user_context = UserContext(roles=[config.role], tenant_id=engine.config.settings.tenant_id)
    steps: List[Tuple[str, float, bool]] = []
    result: Optional[QueryResult] = None
    failure: Optional[str] = None

    started = time.perf_counter()
    presenter.start_interactive_status("Thinking...")
    try:
        for event in engine.stream_query(config.query, datasource_id=config.ds_id,
                                         execute=not config.no_exec, user_context=user_context):
            if event.type == "node_started":
                presenter.update_interactive_status(f"Running {event.data.get('node')}...")
            elif event.type == "node_finished":
                step = (str(event.data.get("node")), float(event.data.get("seconds") or 0.0),
                        bool(event.data.get("ok", True)))
                steps.append(step)
                record_step(step[0], step[1])
            elif event.type == "final":
                result = QueryResult.model_validate(event.data)
            elif event.type == "error":
                failure = event.data.get("message")
    finally:
        presenter.stop_interactive_status()
    duration = time.perf_counter() - started

    if result is None:
        presenter.print_error(failure or "The run ended without a result.")
        sys.exit(1)

    _print_result(presenter, config, result)

    if config.show_perf:
        tokens = {node: totals.total_tokens for node, totals in result.usage.nodes.items()}
        presenter.print_step_timings(steps, tokens)

    presenter.print_usage_summary(duration, result.usage.model_dump())

    if result.trace_path:
        presenter.print_info(f"Trace written to {result.trace_path} (inspect: nl2sql trace show <file>)")

    # The run can finish and still have failed: an ERROR or CRITICAL error (an
    # RBAC refusal included) makes its status "error". Warnings alone do not.
    if result.status == "error":
        sys.exit(1)


def _step_metrics(engine):
    """Records each step's time on ``nl2sql.node.duration``, with the exporter the config names."""
    from nl2sql.common.metrics import configure_metrics, node_duration_histogram

    observability = engine.config.settings.observability
    configure_metrics(exporter_type=observability.exporter, otlp_endpoint=observability.otlp_endpoint)
    return lambda node, seconds: node_duration_histogram.record(seconds, {"node": node})


def _print_result(presenter: ConsolePresenter, config: RunConfig, result: QueryResult) -> None:
    history = [{"sub_query": sq.intent, "datasource_id": sq.datasource_id, "sql": sq.sql}
               for sq in result.sub_queries if sq.intent or sq.sql]

    if config.verbose:
        presenter.print_execution_tree(config.query, history, top_level_reasoning=result.reasoning)
        try:
            with open("last_reasoning.json", "w") as f:
                json.dump({"global_reasoning": result.reasoning, "execution_history": history}, f,
                          indent=2, default=str)
            presenter.print_info("Detailed reasoning trace saved to last_reasoning.json")
        except OSError:
            pass

    used = sorted({item["datasource_id"] for item in history if item["datasource_id"]})
    if used:
        presenter.print_info(f"Datasources used: {', '.join(used)}")
    for item in history:
        if item["sql"]:
            body = Text()
            if item["sub_query"]:
                body.append(f"Sub-Query: {item['sub_query']}\n", style="bold")
            body.append(f"Datasource: {item['datasource_id'] or 'Unknown'}\n\n", style="bold")
            body.append(str(item["sql"]))
            presenter.print_sql(body)

    presenter.print_pipeline_errors(result.errors)
    for warning in result.warnings:
        presenter.print_warning(json.dumps(warning, default=str))

    if result.final_answer:
        presenter.print_answer_synthesizer_output(result.final_answer)

    rows = [sq.rows.total_rows for sq in result.sub_queries if sq.rows is not None]
    if rows:
        presenter.print_rows_returned(sum(rows))
