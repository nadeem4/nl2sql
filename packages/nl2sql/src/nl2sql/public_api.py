"""The ``NL2SQL`` facade: the one thing an application talks to.

Questions are top level (``run_query``, ``stream_query``); everything else is
a namespace (``engine.datasources``, ``engine.index``, ...). See
docs/architecture/configuration_v2.md, section 7.
"""

from __future__ import annotations

import os
import pathlib
from functools import cached_property
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Union

if TYPE_CHECKING:
    from nl2sql.api.curated_api import CuratedAPI
    from nl2sql.api.datasource_api import DatasourcesAPI
    from nl2sql.api.feedback_api import FeedbackAPI
    from nl2sql.api.indexing_api import IndexAPI
    from nl2sql.api.llm_api import LLMAPI
    from nl2sql.api.policy_api import PoliciesAPI
    from nl2sql.api.query_api import QueryAPI, QueryResult
    from nl2sql.api.results_api import ResultsAPI
    from nl2sql.api.traces_api import TracesAPI
    from nl2sql.auth.models import UserContext
    from nl2sql.check import CheckReport
    from nl2sql.common.cancellation import CancellationToken
    from nl2sql.config import LLM, Config
    from nl2sql.pipeline.events import QueryEvent
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

    def _attach(self, runtime: "Runtime") -> None:
        self._runtime = runtime
        self._benchmark = None

    # ------------------------------------------------------------ namespaces
    # Each is built on first use from the runtime; the classes are internal.

    @cached_property
    def datasources(self) -> "DatasourcesAPI":
        """The datasources: ``list``, ``get``, ``add``, ``remove``, ``schema``, ``test``."""
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
        """A run's saved results, by its ``trace_id``: ``manifest``, ``page``, ``csv``."""
        from nl2sql.api.results_api import ResultsAPI

        return ResultsAPI(self._runtime)

    @cached_property
    def traces(self) -> "TracesAPI":
        """Recorded runs: ``get``, ``replay``."""
        from nl2sql.api.traces_api import TracesAPI

        return TracesAPI(self._runtime)

    @cached_property
    def feedback(self) -> "FeedbackAPI":
        """Answer ratings and the guardrail rates: ``record``, ``list``, ``stats``, ``export``, ``clear``."""
        from nl2sql.api.feedback_api import FeedbackAPI

        return FeedbackAPI(self._runtime)

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

    @property
    def benchmark(self):
        """The benchmark API, built on first use so ``import nl2sql`` never loads ``nl2sql.evaluation``."""
        if self._benchmark is None:
            from nl2sql.api.benchmark_api import BenchmarkAPI

            self._benchmark = BenchmarkAPI(self._runtime)
        return self._benchmark

    # ------------------------------------------------------------ questions

    def run_query(self, question: str, *, datasource_id: Optional[str] = None, execute: bool = True,
                  user_context: Optional["UserContext"] = None,
                  cancellation_token: Optional["CancellationToken"] = None) -> "QueryResult":
        """Answer ``question``: plan, check, generate and run the SQL, and write the answer.

        ``datasource_id`` skips routing; ``execute=False`` stops after the SQL
        is generated (plan only). ``user_context`` carries the caller's roles.
        Pass a :class:`~nl2sql.CancellationToken` and call its ``cancel()``
        from another thread to stop the run before its next step.
        """
        self._ensure_open()
        return self._queries.run_query(question, datasource_id=datasource_id, execute=execute,
                                       user_context=user_context, cancellation_token=cancellation_token)

    def stream_query(self, question: str, *, datasource_id: Optional[str] = None, execute: bool = True,
                     user_context: Optional["UserContext"] = None,
                     cancellation_token: Optional["CancellationToken"] = None) -> Iterator["QueryEvent"]:
        """
        Run a question as ``run_query`` does, yielding :class:`~nl2sql.QueryEvent`
        values as it goes: ``node_started`` / ``node_finished`` per step,
        ``result_manifest`` when the results are saved, ``answer_delta`` as the
        answer is written, then ``final`` (``run_query``'s result, as JSON) or
        ``error``. ``event.sse()`` is the event as one Server-Sent Event.
        Closing the iterator early cancels the run.
        """
        self._ensure_open()
        return self._queries.stream_query(question, datasource_id=datasource_id, execute=execute,
                                          user_context=user_context, cancellation_token=cancellation_token)


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
