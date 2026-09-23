"""Every step of a run, in order, and which of them call a model.

The pitch of this engine is that the model plans and deterministic code writes
and checks the SQL. That is invisible while a run is only a list of node names,
so this module says, once, what each node of the pipeline decides and whether a
model decides it. The playground's Pipeline page is the reader; the CLI and the
docs can read the same list.

The order is the order :func:`nl2sql.pipeline.graph.build_graph` and
:func:`nl2sql.pipeline.subgraphs.sql_agent.build_sql_agent_graph` run their
nodes, with the SQL agent's own nodes nested under it, exactly as the Debug
ledger nests them. The five steps with an ``agent`` are the LLM nodes, and that
mapping is :data:`nl2sql.llm.providers.LLM_AGENTS` rather than a second copy of
it. ``tests/unit/test_pipeline_steps.py`` builds both graphs and fails when a
node is added, renamed or removed and this list does not follow.
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

from nl2sql.llm.providers import LLM_AGENTS

__all__ = ["PIPELINE_STEPS", "SQL_AGENT_STEP", "PipelineStep", "describe_pipeline"]

# The node the SQL agent's own nodes are nested under.
SQL_AGENT_STEP = "sql_agent"


class PipelineStep(NamedTuple):
    """One node of a run.

    Attributes:
        node: The graph node name, which is also the name usage and timings are
            recorded under, so a run's telemetry joins to this list by it.
        label: What the step is, in plain words.
        does: One sentence on what it decides.
        agent: The LLM agent name for a step a model runs, else None.
        parent: The step this one runs inside, or None for a top-level step.
    """

    node: str
    label: str
    does: str
    agent: Optional[str] = None
    parent: Optional[str] = None

    @property
    def kind(self) -> str:
        """``"model"`` when a model decides this step, ``"code"`` when code does."""
        return "model" if self.agent else "code"


def _step(node: str, label: str, does: str, parent: Optional[str] = None) -> PipelineStep:
    """One step, taking its agent from ``LLM_AGENTS`` so the two cannot drift."""
    return PipelineStep(node=node, label=label, does=does, agent=LLM_AGENTS.get(node), parent=parent)


PIPELINE_STEPS: List[PipelineStep] = [
    _step("datasource_resolver", "Answerability check",
          "Decides whether the connected databases can answer the question at all, and which one "
          "it is about, before anything else runs."),
    _step("decomposer", "Question splitter",
          "Breaks the question into the sub-queries that have to be answered, and says how their "
          "answers combine."),
    _step("global_planner", "Execution plan",
          "Arranges those sub-queries into a dependency graph: which can run together, which wait "
          "on another's rows."),
    _step("layer_router", "Layer router",
          "Sends the next layer of sub-queries to the SQL agent, and stops when every one of them "
          "has an answer."),
    _step(SQL_AGENT_STEP, "SQL agent",
          "Runs one sub-query end to end through the steps below, once per sub-query and again "
          "for each retry."),
    _step("schema_retriever", "Schema retrieval",
          "Searches the index for the tables and columns this sub-query is about, so the planner "
          "sees a handful of them rather than the whole database.", parent=SQL_AGENT_STEP),
    _step("ast_planner", "Query planner",
          "Writes a structured plan of tables, joins, filters and grouping, and never SQL text. "
          "Most of a run's tokens are spent here.", parent=SQL_AGENT_STEP),
    _step("logical_validator", "Plan checks",
          "Checks the plan against the real schema and this role's policy, and rejects it when a "
          "table, a column or a join is wrong.", parent=SQL_AGENT_STEP),
    _step("retry_handler", "Retry bookkeeping",
          "Counts the attempt and hands the rejected plan and the reasons to the repair step.",
          parent=SQL_AGENT_STEP),
    _step("refiner", "Plan repair",
          "Rewrites a plan the checks rejected, using the reasons they gave. Runs only on a "
          "retry.", parent=SQL_AGENT_STEP),
    _step("generator", "SQL writer",
          "Renders the checked plan into SQL for this database's dialect with sqlglot. No model "
          "writes the SQL.", parent=SQL_AGENT_STEP),
    _step("executor", "Executor",
          "Runs the SQL read-only, under the row limit and the timeout, and keeps the rows.",
          parent=SQL_AGENT_STEP),
    _step("aggregator", "Result combiner",
          "Joins, unions and post-processes the sub-query results into one result set, in code."),
    _step("answer_synthesizer", "Answer writer",
          "Reads the rows that came back and writes the sentence that answers the question."),
]


def describe_pipeline(agents: Optional[Dict[str, Any]] = None,
                      default: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The steps as JSON, each model step carrying the model configured for it.

    Args:
        agents: Agent name -> its configured entry, as
            :meth:`nl2sql.llm.registry.LLMRegistry.list_llms` returns it. A
            model step with no entry of its own runs on ``default``.
        default: The default agent's entry, used by every step that has none.

    Returns:
        One dict per step: its node, label, sentence, kind, parent, and -- for a
        model step -- the provider and model it will run on. Never a key: the
        entries this reads already exclude it.
    """
    configured = agents or {}
    fallback = default or configured.get("default") or {}
    described = []
    for step in PIPELINE_STEPS:
        entry = (configured.get(step.agent) or fallback) if step.agent else {}
        described.append({
            "node": step.node,
            "label": step.label,
            "does": step.does,
            "kind": step.kind,
            "agent": step.agent,
            "parent": step.parent,
            "provider": entry.get("provider") if step.agent else None,
            "model": entry.get("model") if step.agent else None,
        })
    return described
