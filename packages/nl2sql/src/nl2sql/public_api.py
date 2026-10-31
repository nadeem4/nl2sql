"""The ``NL2SQL`` facade: the one thing an application talks to.

Questions are top level (``run_query``, ``stream_query``); everything else is
a namespace (``engine.datasources``, ``engine.index``, ...). See
docs/architecture/configuration_v2.md, section 7.
"""

from __future__ import annotations

import os
import pathlib
from functools import cached_property
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, Optional, Sequence, Union

if TYPE_CHECKING:
    from nl2sql.api.api_keys_api import ApiKeysAPI
    from nl2sql.api.chat_tools_api import ChatToolsAPI
    from nl2sql.api.conversations_api import ConversationsAPI
    from nl2sql.api.curated_api import CuratedAPI
    from nl2sql.api.dashboards_api import DashboardsAPI
    from nl2sql.api.workspaces_api import WorkspacesAPI
    from nl2sql.api.datasource_api import DatasourcesAPI
    from nl2sql.api.evals_api import EvalsAPI
    from nl2sql.api.events_api import EventsAPI
    from nl2sql.api.feedback_api import FeedbackAPI
    from nl2sql.api.indexing_api import IndexAPI
    from nl2sql.api.knowledge_api import KnowledgeAPI
    from nl2sql.api.llm_api import LLMAPI
    from nl2sql.api.policy_api import PoliciesAPI
    from nl2sql.api.proposals_api import ProposalsAPI
    from nl2sql.api.query_api import QueryAPI, QueryResult
    from nl2sql.api.results_api import ResultsAPI
    from nl2sql.api.storage_api import StorageAPI
    from nl2sql.api.traces_api import TracesAPI
    from nl2sql.policies import UserContext
    from nl2sql.clarify.models import ClarificationChoice
    from nl2sql.hooks.approval import Approval
    from nl2sql.llm.override import LLMOverride
    from nl2sql.pipeline.pinned import SuppliedPlan
    from nl2sql.check import CheckReport
    from nl2sql.common.cancellation import CancellationToken
    from nl2sql.config import LLM, Config
    from nl2sql_sdk.events import Event
    from nl2sql.registry import Registry
    from nl2sql.runtime import Runtime


class NL2SQL:
    """The engine: built from one ``Config``, and the one thing an application talks to.

    ``NL2SQL(config)``, ``NL2SQL.from_config(path)`` and ``NL2SQL.demo()``
    build it. Building it never connects to a datasource or calls a model;
    ``check()`` does, and ``close()`` releases what it opened. See
    docs/architecture/configuration_v2.md, section 4.
    """

    def __init__(self, config: "Config", *, registry: Optional["Registry"] = None):
        """Load and validate everything ``config`` names. Never connects.

        Resolves every name through ``registry`` (``Registry.default()`` when
        None), imports only the components the config uses, and builds the
        secret manager, the adapters (not connected), the LLM clients (no
        model call), the embedder and the stores under ``storage.path``.

        Raises:
            ConfigError: one error listing every problem found.
        """
        # The runtime is imported here, not at the top of the module, so
        # ``import nl2sql`` stays light (docs/architecture/invariants.md).
        from nl2sql.runtime import Runtime

        self._attach(Runtime.from_config(config, registry))

    @classmethod
    def from_config(cls, path: Optional[Union[str, os.PathLike]] = None, *,
                    registry: Optional["Registry"] = None) -> "NL2SQL":
        """``NL2SQL(load_config(path), registry=registry)``.

        ``path`` defaults to ``$NL2SQL_CONFIG``, then ``./nl2sql.yaml``.
        """
        from nl2sql.config import load_config

        return cls(load_config(path), registry=registry)

    @classmethod
    def demo(cls, *, llm: Optional["LLM"] = None,
             path: Union[str, os.PathLike] = ".nl2sql-demo") -> "NL2SQL":
        """The demo: three bundled SQLite databases, opened read-only from the installed package.

        With the demo's roles (admin, analyst, viewer) and verified queries,
        and its data under ``path``. Without ``llm``, the first provider
        whose key variable is set is used, with its default model.

        Raises:
            ConfigError: no ``llm`` was given and no provider key is set.
        """
        from nl2sql.config import demo_config

        return cls(demo_config(llm=llm if llm is not None else _demo_llm(), path=pathlib.Path(path)))

    def add_demo(self, *, user_context: Optional["UserContext"] = None,
                 on_datasource: Optional[Callable[[Any], None]] = None):
        """Try the demo: add the sample databases and the demo glossary to this engine, then index them.

        See :meth:`DatasourcesAPI.add_demo`. Returns the build's ``RebuildResult``.
        """
        self._ensure_open()
        return self.datasources.add_demo(user_context=user_context, on_datasource=on_datasource)

    def _attach(self, runtime: "Runtime") -> None:
        self._runtime = runtime

    # ------------------------------------------------------------ namespaces
    # Each is built on first use from the runtime; the classes are internal.

    @cached_property
    def datasources(self) -> "DatasourcesAPI":
        """The datasources: ``list``, ``get``, ``add``, ``remove``, ``schema``, ``types``, ``test``, ``scan``,
        ``diagram``, ``save``."""
        from nl2sql.api.datasource_api import DatasourcesAPI

        return DatasourcesAPI(self._runtime)

    @cached_property
    def index(self) -> "IndexAPI":
        """The vector index: ``build``, ``health``, ``clear``, ``inspect``, ``inspect_defaults``."""
        from nl2sql.api.indexing_api import IndexAPI

        return IndexAPI(self._runtime)

    @cached_property
    def llm(self) -> "LLMAPI":
        """The models the agents run on: ``list``, ``get``, ``providers``, ``replace``."""
        from nl2sql.api.llm_api import LLMAPI

        return LLMAPI(self._runtime)

    @cached_property
    def policies(self) -> "PoliciesAPI":
        """Who may read what: ``roles``, ``allows``, ``allowed``, ``validate``."""
        from nl2sql.api.policy_api import PoliciesAPI

        return PoliciesAPI(self._runtime)

    @cached_property
    def curated(self) -> "CuratedAPI":
        """The curated answers: ``validate``, ``test``."""
        from nl2sql.api.curated_api import CuratedAPI

        return CuratedAPI(self._runtime)

    @cached_property
    def results(self) -> "ResultsAPI":
        """A run's saved results, by its ``trace_id``: ``manifest``, ``page``, ``csv``, ``chart``."""
        from nl2sql.api.results_api import ResultsAPI

        return ResultsAPI(self._runtime)

    @cached_property
    def traces(self) -> "TracesAPI":
        """Recorded runs: ``get``, ``replay``."""
        from nl2sql.api.traces_api import TracesAPI

        return TracesAPI(self._runtime)

    @cached_property
    def storage(self) -> "StorageAPI":
        """Where the engine keeps its data, and retention: ``info``, ``purge``."""
        from nl2sql.api.storage_api import StorageAPI

        return StorageAPI(self._runtime)

    @cached_property
    def feedback(self) -> "FeedbackAPI":
        """Answer ratings and the guardrail rates: ``record``, ``list``, ``stats``, ``export``, ``clear``."""
        from nl2sql.api.feedback_api import FeedbackAPI

        return FeedbackAPI(self._runtime)

    @cached_property
    def events(self) -> "EventsAPI":
        """The engine's events, every run's and the engine's own: ``subscribe``, ``catalog``."""
        from nl2sql.api.events_api import EventsAPI

        return EventsAPI(self._runtime)

    @cached_property
    def evals(self) -> "EvalsAPI":
        """Evals: load and run suites, regenerate their rows, list the scorers. Builds no evaluation code until used."""
        from nl2sql.api.evals_api import EvalsAPI

        return EvalsAPI(self._runtime)

    @cached_property
    def knowledge(self) -> "KnowledgeAPI":
        """The glossary as the engine uses it: ``sources``, ``show``, ``export``, ``reload``, ``profile``,
        ``save_choice``. Every read takes the caller."""
        from nl2sql.api.knowledge_api import KnowledgeAPI

        return KnowledgeAPI(self._runtime)

    @cached_property
    def proposals(self) -> "ProposalsAPI":
        """The review queue: ``list``, ``get``, ``accept``, ``reject``, ``export``, ``apply``."""
        from nl2sql.api.proposals_api import ProposalsAPI

        return ProposalsAPI(self._runtime)

    @cached_property
    def conversations(self) -> "ConversationsAPI":
        """Model-led conversations over the database: ``start``, ``ask``, ``stream``, ``retry``, ``tool``,
        ``ask_only``, ``report``, ``get``, ``list``, ``delete``. Every method takes the caller."""
        from nl2sql.api.conversations_api import ConversationsAPI

        return ConversationsAPI(self._runtime, self)

    @cached_property
    def chat_tools(self) -> "ChatToolsAPI":
        """The chat tools a conversation can call: ``list``."""
        from nl2sql.api.chat_tools_api import ChatToolsAPI

        return ChatToolsAPI(self._runtime)

    @cached_property
    def workspaces(self) -> "WorkspacesAPI":
        """The folders dashboards live in: ``create``, ``rename``, ``list``, ``delete``. Every method takes the
        caller."""
        from nl2sql.api.workspaces_api import WorkspacesAPI

        return WorkspacesAPI(self._runtime)

    @cached_property
    def dashboards(self) -> "DashboardsAPI":
        """Dashboards: ``get``, ``list``, ``open``, ``refresh``, ``edit`` and the named edits, ``pin``, ``move``,
        ``delete``, ``generate``, ``filter_values``, ``suggest_questions``, ``run_scheduled``. Every method
        takes the caller, except ``run_scheduled``."""
        from nl2sql.api.dashboards_api import DashboardsAPI

        return DashboardsAPI(self._runtime)

    @cached_property
    def api_keys(self) -> "ApiKeysAPI":
        """Bearer keys for MCP over HTTP: ``create``, ``list``, ``revoke``, ``verify``."""
        from nl2sql.api.api_keys_api import ApiKeysAPI

        return ApiKeysAPI(self._runtime)

    @cached_property
    def _queries(self) -> "QueryAPI":
        from nl2sql.api.query_api import QueryAPI

        return QueryAPI(self._runtime)

    # ------------------------------------------------------------ lifecycle

    @property
    def config(self) -> "Config":
        """The current frozen ``Config``. Secrets appear as references only."""
        return self._runtime.config

    def about(self) -> Dict[str, Any]:
        """The engine, and every step of a run with the model each model step is configured on.

        ``version`` (of ``nl2sql-engine``), ``git_sha`` (when running from a
        checkout), ``python``, and ``steps``: one dict per pipeline step in the
        order a run takes them, with its ``node``, ``label``, what it ``does``,
        its ``kind`` (``model`` or ``code``), its ``parent`` (the SQL agent, for
        the steps nested in it) and, for a model step, its ``agent`` and the
        ``provider`` and ``model`` it runs on. Never a key.
        """
        from nl2sql.llm.providers import LLM_AGENTS
        from nl2sql.pipeline.steps import describe_pipeline
        from nl2sql.tracing.trace import engine_info

        try:
            # The steps name each agent as the nodes ask for it (``astplanner``).
            agents = {LLM_AGENTS.get(name, name): entry for name, entry in self.llm.list().items()}
        except Exception:  # an engine whose LLMs cannot be listed still has its steps
            agents = {}
        return {**engine_info(), "steps": describe_pipeline(agents)}

    def check(self) -> "CheckReport":
        """Connect to each datasource, check every agent's API key resolves, and report the index.

        A datasource that fails is marked unavailable and left out of routing;
        the engine runs degraded. No model is called. Safe to call again.

        Raises:
            EngineNotReady: a ``required`` datasource failed, or datasources
                are configured and none is ready.
        """
        from nl2sql.check import CheckReport, EngineNotReady

        self._ensure_open()
        statuses = self._runtime.check_datasources()
        report = CheckReport(datasources=statuses, llm=self._runtime.llm_registry.missing_keys(),
                             index=self.index.health()["status"])
        required = [d.id for d in statuses if d.required and d.status == "failed"]
        if required:
            raise EngineNotReady(f"The required datasource(s) {', '.join(required)} failed.", report)
        if statuses and not any(d.status == "ready" for d in statuses):
            raise EngineNotReady("No datasource is ready.", report)
        return report

    def close(self) -> None:
        """Dispose the adapters' pools and close the stores. The engine cannot be used afterwards.

        Calling it twice is fine.
        """
        self._runtime.close()

    @property
    def closed(self) -> bool:
        return self._runtime.closed

    def __enter__(self) -> "NL2SQL":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _ensure_open(self) -> None:
        runtime = getattr(self, "_runtime", None)
        if getattr(runtime, "closed", False):
            raise RuntimeError("This engine is closed. Build a new one.")

    # ------------------------------------------------------------ questions

    def run_query(self, question: str, *, datasource_id: Optional[str] = None, execute: bool = True,
                  user_context: Optional["UserContext"] = None,
                  cancellation_token: Optional["CancellationToken"] = None,
                  plan: Optional["SuppliedPlan"] = None,
                  approvals: Optional[Sequence["Approval"]] = None, clarify: bool = True,
                  choice: Optional["ClarificationChoice"] = None, answer: bool = True,
                  llm_override: Optional["LLMOverride"] = None) -> "QueryResult":
        """Answer ``question``: plan, check, generate and run the SQL, and write the answer.

        ``datasource_id`` skips routing; ``execute=False`` stops after the SQL
        is generated (plan only). ``user_context`` carries the caller's roles.
        Pass a :class:`~nl2sql.CancellationToken` and call its ``cancel()``
        from another thread to stop the run before its next step.

        ``plan`` (a :class:`~nl2sql.SuppliedPlan`) runs a plan the caller
        holds: no routing, decomposing or planning, but its datasource is
        checked against the caller's scope and the plan passes the
        ``plan.after`` hooks, the validator and the generator; it is never
        cached. ``approvals`` answer an earlier run's ``approvals_needed``:
        re-run the question with the asker's ``user_context`` and an
        :class:`~nl2sql.Approval` per request. The engine checks each one and
        pins the approved plan (``plan_source="approved"``).

        When the question can be read more than one way and two or three
        readings pass the checks, the run ends with ``status ==
        "needs_clarification"``: nothing is executed, and
        ``result.clarification`` holds the options, each a label, a plan and
        its explanation. ``clarify=False`` runs the most likely reading
        instead and lists the others as assumptions in ``result.explanation``.
        To answer one, ask the same question again as the same caller with
        ``choice=ClarificationChoice(request=result.clarification, option_id="b")``:
        the chosen option's plan runs through the validator
        (``plan_source="chosen"``), without routing or planning. A request that
        is not this question's or this caller's ends the run
        ``CLARIFICATION_INVALID``; one the data or glossary has outdated,
        ``CLARIFICATION_STALE``. Not with ``plan`` or ``approvals``.

        ``answer=False`` skips the answer synthesizer: ``final_answer`` is
        None and the run makes no model call for prose (a dashboard refresh
        re-runs stored plans with ``plan=`` and ``answer=False``, and no
        model call at all).

        ``llm_override`` (a :class:`~nl2sql.LLMOverride`) gives this one call the
        caller's own keys and models in place of the configured ones (D210): its
        ``keys`` map a provider to the caller's key, its ``models`` map an agent
        name to ``(provider, model)``. A model runs only on a key the override
        carries for its provider, else ``MissingProviderKey`` is raised before
        anything runs, so a caller's choice never lands on the operator's
        account. The keys live for the call: they are in no environment variable
        and no cache, and the trace masks them. With a replay provider (G2b) an
        override key replaces the recording for this call.
        """
        self._ensure_open()
        return self._queries.run_query(question, datasource_id=datasource_id, execute=execute,
                                       user_context=user_context, cancellation_token=cancellation_token,
                                       plan=plan, approvals=approvals, clarify=clarify, choice=choice,
                                       answer=answer, llm_override=llm_override)

    def stream_query(self, question: str, *, datasource_id: Optional[str] = None, execute: bool = True,
                     user_context: Optional["UserContext"] = None,
                     cancellation_token: Optional["CancellationToken"] = None,
                     plan: Optional["SuppliedPlan"] = None,
                     approvals: Optional[Sequence["Approval"]] = None,
                     clarify: bool = True, choice: Optional["ClarificationChoice"] = None,
                     answer: bool = True, llm_override: Optional["LLMOverride"] = None) -> Iterator["Event"]:
        """
        Run a question as ``run_query`` does, yielding its :class:`~nl2sql.Event`
        values as they are published, from the caller-safe allow-list:
        ``run.started``, ``node.started`` / ``node.finished`` per step,
        ``result.manifest`` when the results are saved, ``answer.delta`` as the
        answer is written, ``sanity.flagged`` and ``run.limit_reached``, then
        exactly one of ``run.finished`` (whose ``data.result`` is
        ``run_query``'s result, as JSON) or ``run.failed``. Operator events
        (``policy.decision``, ``hook.decision``, ``llm.call``) are never on
        it: subscribe to ``events`` for those. ``event.sse()`` is the event as
        one Server-Sent Event. Closing the iterator early cancels the run.
        ``plan``, ``approvals``, ``clarify``, ``choice``, ``answer`` and ``llm_override`` are as for :meth:`run_query`;
        a refused override raises at the call.
        """
        self._ensure_open()
        return self._queries.stream_query(question, datasource_id=datasource_id, execute=execute,
                                          user_context=user_context, cancellation_token=cancellation_token,
                                          plan=plan, approvals=approvals, clarify=clarify, choice=choice,
                                          answer=answer, llm_override=llm_override)

    def clear_plan_cache(self) -> int:
        """Remove every cached plan from the state store; returns how many there were.

        Schema snapshots and feedback stay. The next run of each question calls the planner.
        """
        self._ensure_open()
        return self._runtime.state.clear_plans()


def _demo_llm() -> "LLM":
    """The demo's LLM when none is given: the first provider whose key variable is set."""
    from nl2sql.config import ConfigError, Problem, default_llm
    from nl2sql.llm.providers import DEFAULT_MODELS, KEYED_PROVIDERS, api_key_from_env, env_var_for_provider

    providers = [name for name in KEYED_PROVIDERS if name in DEFAULT_MODELS]
    for provider in providers:
        if api_key_from_env(provider):
            return default_llm(provider)
    variables = ", ".join(env_var_for_provider(p) for p in providers)
    raise ConfigError([Problem(
        file="NL2SQL.demo()", path="llm", message=f"No LLM was given and none of {variables} is set.",
        fix="Set one of them, or pass llm=LLM(provider=..., model=...).")], source="NL2SQL.demo()")
