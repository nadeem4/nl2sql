"""``engine.knowledge``: the glossary as the engine uses it, the profiler, and saving a clarification pick.

See docs/architecture/wave_b.md, sections 3, 6 and 7. Every method that shows
knowledge or schema takes the caller as a required keyword and shows only what
the caller's scope reaches (D60): there is no admin default. Each method has its
final signature; the Wave B PR named in its docstring builds it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

if TYPE_CHECKING:
    from nl2sql.common.cancellation import CancellationToken
    from nl2sql.clarify.models import ClarificationChoice
    from nl2sql.knowledge.models import DatasourceKnowledgeView, KnowledgeSourceInfo
    from nl2sql.policies import UserContext
    from nl2sql.profiler.models import ProfileReport
    from nl2sql_sdk.stores import Proposal


HIDDEN_ERROR = "The last reload failed; the previous knowledge is still in use. An admin can see why."


def _caller(user_context: Any) -> "UserContext":
    """The caller, required: there is no default view (D60)."""
    from nl2sql.policies import UserContext

    if not isinstance(user_context, UserContext):
        raise TypeError("user_context is required: pass the caller's UserContext. There is no default caller; "
                        "an admin is a caller with an admin role.")
    return user_context


class KnowledgeAPI:
    """The engine's knowledge. Internal: reach it through ``NL2SQL.knowledge``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def sources(self, *, user_context: "UserContext") -> List["KnowledgeSourceInfo"]:
        """Each knowledge source, in config order: its type, how many items of each kind it holds per
        datasource the caller reaches (counting only what the caller may see), the SHA-256 of the file it
        read, when it loaded, and the last reload's error. The error itself is
        shown only to a caller who may reload (an admin role, or ``allow_all``):
        it quotes the file. Anyone else is told only that the reload failed.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
        """
        from nl2sql.knowledge.merge import counts
        from nl2sql.knowledge.models import KnowledgeSourceInfo
        from nl2sql.knowledge.scope import deny_names, visible

        caller = _caller(user_context)
        knowledge = self._ctx.knowledge
        may_reload = self._may_reload(caller)
        reached: Dict[str, Any] = {}
        infos = []
        for source in knowledge.sources:
            per_datasource = {}
            for ds_id, block in source.glossary.datasources.items():
                if ds_id not in reached:
                    reached[ds_id] = self._reach(ds_id, caller)
                if reached[ds_id] is not None:
                    scope, snapshot = reached[ds_id]
                    per_datasource[ds_id] = counts(visible(block, snapshot, scope.allows, deny_names(scope)))
            error = knowledge.errors.get(source.type)
            if error is not None and not may_reload:
                # The error quotes the file: its path, its lines and any table it names (D60).
                error = HIDDEN_ERROR
            infos.append(KnowledgeSourceInfo(type=source.type, counts=per_datasource, sha256=source.sha256,
                                             loaded_at=source.loaded_at, error=error))
        return infos

    def show(self, datasource_id: Optional[str] = None, *,
             user_context: "UserContext") -> List["DatasourceKnowledgeView"]:
        """The merged knowledge as the engine uses it, per datasource the caller reaches.

        Each item carries its origin (``knowledge``, ``database`` or
        ``derived``, D38) and the source that set it; the roles the snapshot
        proves are included (D61). ``problems`` lists the names that do not
        resolve against the live snapshot and the joins whose types cannot be
        compared. An item on a table the caller cannot read, or whose text
        mentions one, is left out (D39, D60). A datasource not indexed yet
        shows nothing but that problem: without its schema, names cannot be
        checked against the caller's scope.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``: there is no default caller.
            ValueError: ``datasource_id`` is not configured.
        """
        caller = _caller(user_context)
        views = []
        for ds_id in self._ids([datasource_id] if datasource_id is not None else None):
            view = self._view(ds_id, caller)
            if view is not None:
                views.append(view)
        return views

    def export(self, path: Optional[Union[str, os.PathLike]] = None, *, datasource_ids: Optional[Sequence[str]] = None,
               user_context: "UserContext") -> str:
        """The knowledge the caller may see, from the knowledge sources only, as one canonical ``glossary.yaml``.

        Only datasources the caller reaches and that are indexed are
        exported, each filtered as ``show`` filters it. Derived roles and the
        database's own comments are not exported: they are read from the
        schema again. With ``path``, the text is also written there.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: a datasource is not configured, or ``path`` is a config
                file or a knowledge source's own file, which export never overwrites.
        """
        from nl2sql.knowledge.scope import deny_names, visible
        from nl2sql.knowledge.yaml_source import canonical_yaml
        from nl2sql_sdk.knowledge import Glossary

        caller = _caller(user_context)
        knowledge = self._ctx.knowledge
        datasources = {}
        for ds_id in self._ids(datasource_ids):
            reach = self._reach(ds_id, caller) if ds_id in knowledge.datasources else None
            if reach is not None:
                scope, snapshot = reach
                datasources[ds_id] = visible(knowledge.get(ds_id), snapshot, scope.allows, deny_names(scope))
        # Each item was checked against the cap when its source loaded.
        text = canonical_yaml(Glossary.model_validate({"version": 1, "datasources": datasources},
                                                      context={"item_max_chars": 1 << 30}))
        if path is not None:
            target = Path(path)
            if Path(os.path.realpath(target)) in self._protected_files():
                raise ValueError(f"export will not overwrite {target.as_posix()}: it is a config file or a "
                                 f"knowledge source's own file. Export to another path.")
            target.write_text(text, encoding="utf-8")
        return text

    def reload(self, *, user_context: "UserContext") -> List["KnowledgeSourceInfo"]:
        """Load every source again and swap atomically; needs an admin role (``policies.admin_roles``).

        Runs in flight keep the knowledge they started with. On a failure the
        previous knowledge stays live and the failing source's ``error`` says
        why; ``knowledge.loaded`` is published either way. Under
        ``policies.allow_all`` every caller may reload.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            PermissionError: the caller holds no admin role.
        """
        caller = _caller(user_context)
        policies = self._ctx.config.policies
        if not self._may_reload(caller):
            raise PermissionError(f"Reloading the knowledge needs one of the admin roles "
                                  f"({', '.join(policies.admin_roles) or 'none defined'}).")
        self._ctx.reload_knowledge()
        return self.sources(user_context=caller)

    def profile(self, datasource_ids: Optional[Sequence[str]] = None, *, user_context: "UserContext",
                draft: bool = False, max_cost_usd: Optional[float] = None,
                on_datasource: Optional[Callable[["ProfileReport"], None]] = None,
                cancellation_token: Optional["CancellationToken"] = None) -> List["ProfileReport"]:
        """Profile each datasource's live snapshot, probing only the tables the caller reaches, and file what
        it finds as proposals (``origin: profiler``): joins the database does not declare (by name, type and a
        bounded value-overlap probe) and heuristic column roles. Nothing is written but the review queue (D38).

        One report per datasource the caller reaches. A datasource that is not indexed yet is reported with an
        issue and skipped: the profiler reads the live build's snapshot and never reflects on its own. The
        bounds are ``settings.profiler`` (``max_probes``, ``time_budget_s``, ...); each finished profile
        publishes ``profile.finished``.

        ``draft=True`` also has the ``profiler`` model draft the table and column descriptions (and column
        synonyms) that neither the glossary nor the database has, as ``origin: draft`` proposals: a draft
        reaches no prompt and no index until a person accepts it (D38). A column's drafted description and its
        role are one proposal. The model's calls are capped at ``max_cost_usd`` across the whole call
        (``settings.profiler.max_cost_usd`` when None); a run that reaches the cap reports
        ``stopped_by: "cost_cap"`` with what it drafted so far. With no model for the agent, nothing is drafted
        and each report says so in ``issues``.

        ``on_datasource`` gets each datasource's report as it finishes. Cancel ``cancellation_token`` to stop
        between datasources: the one in flight finishes, and the reports so far are returned.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: a datasource is not configured, or ``max_cost_usd`` is not positive.
            UnknownPriceError: ``draft`` is set and the ``profiler`` model has no price in ``llm.prices``.
        """
        from nl2sql.profiler.run import profile

        caller = _caller(user_context)
        return profile(self._ctx, self._ids(datasource_ids), caller, draft=draft, max_cost_usd=max_cost_usd,
                       on_datasource=on_datasource, cancellation_token=cancellation_token)

    def save_choice(self, choice: "ClarificationChoice", *, user_context: "UserContext",
                    term: Optional[str] = None) -> "Proposal":
        """Turn a clarification pick into a ``term`` proposal (D50): the phrase (or ``term``) as the term's name,
        the chosen option's label as its definition, and the columns and tables its plan reads.

        It reaches the glossary only when an admin accepts it (``engine.proposals.accept``); a term of that name
        replaces the existing one, which ``nl2sql review show`` shows first. Once accepted, the phrase is a term's
        name or synonym, which settles it: the question is no longer asked. Anyone whose scope reaches the
        option's tables may save a pick.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: the request was changed, or has no such option.
            ReviewError: ``NOT_FOUND``, the caller's scope does not reach the option's datasource and tables.
        """
        from nl2sql.clarify.choice import choice_proposal
        from nl2sql.review.models import ReviewError, ReviewErrorCode
        from nl2sql.review.queue import propose, visible

        caller = _caller(user_context)
        proposal = choice_proposal(self._ctx, choice, caller, term)
        if not visible(self._ctx, caller, proposal):
            raise ReviewError(ReviewErrorCode.NOT_FOUND, "That clarification is not one this caller's scope reaches.")
        return propose(self._ctx, proposal)

    # ------------------------------------------------------------ helpers

    def _may_reload(self, caller: "UserContext") -> bool:
        policies = self._ctx.config.policies
        return policies.allow_all or bool(set(caller.roles) & set(policies.admin_roles))

    def _ids(self, datasource_ids: Optional[Sequence[str]]) -> List[str]:
        configured = [d.id for d in self._ctx.config.datasources]
        if datasource_ids is None:
            return configured
        unknown = [i for i in datasource_ids if i not in configured]
        if unknown:
            raise ValueError(f"Unknown datasource ID: {', '.join(unknown)}")
        return list(datasource_ids)

    def _reach(self, datasource_id: str, caller: "UserContext") -> Optional[Tuple[Any, Any]]:
        """The caller's scope and the datasource's snapshot; None when the caller cannot reach the datasource
        or it has no snapshot yet (fails closed)."""
        scope = self._ctx.policy_engine.scope(caller, datasource_id)
        if not scope.reaches_datasource():
            return None
        snapshot = self._ctx.vector_store.snapshot(datasource_id)
        return (scope, snapshot) if snapshot is not None else None

    def _view(self, datasource_id: str, caller: "UserContext") -> Optional["DatasourceKnowledgeView"]:
        from nl2sql.knowledge.models import DatasourceKnowledgeView
        from nl2sql.knowledge.resolve import problems
        from nl2sql.knowledge.scope import deny_names, visible, with_schema_facts

        scope = self._ctx.policy_engine.scope(caller, datasource_id)
        if not scope.reaches_datasource():
            return None
        knowledge = self._ctx.knowledge
        digest = knowledge.digest(datasource_id)
        snapshot = self._ctx.vector_store.snapshot(datasource_id)
        if snapshot is None:
            return DatasourceKnowledgeView(datasource_id=datasource_id, digest=digest, problems=[
                f"{datasource_id} is not indexed yet, so its knowledge cannot be checked against its schema or "
                f"the caller's scope. Run `nl2sql index`."])
        try:
            dialect = self._ctx.ds_registry.get_dialect(datasource_id)
        except ValueError:  # unavailable: join types are not compared
            dialect = None
        denied = deny_names(scope)
        shown = visible(knowledge.get(datasource_id), snapshot, scope.allows, denied)
        full, origins, sources = with_schema_facts(shown, knowledge.provenance.get(datasource_id, {}), snapshot,
                                                   scope.allows, dialect=dialect, also_denied=denied)
        return DatasourceKnowledgeView(datasource_id=datasource_id, digest=digest, knowledge=full, origins=origins,
                                       sources=sources, problems=problems(shown, snapshot, dialect=dialect))

    def _protected_files(self) -> Set[Path]:
        """The config's own files and each knowledge source's file, as real paths."""
        config = self._ctx.config
        files = [source.path for source in self._ctx.knowledge.sources if source.path is not None]
        if config.folder is not None:
            files.append(config.folder / "nl2sql.yaml")
            for kind, label in config._sources.values():
                labels = label if kind == "folder" else [label] if kind == "file" else []
                files.extend(config.folder / item for item in labels)
        return {Path(os.path.realpath(f)) for f in files}
