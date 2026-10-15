from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.config.curated import inspect_verified_sql
from nl2sql.indexing.retrieval import CURATED_TYPE
from nl2sql_sdk.schema import TableRef
from nl2sql.policies import UserContext
from nl2sql.common.logger import get_logger
from nl2sql.events import publish
from nl2sql.hooks import hooks_of
from nl2sql.runtime import Runtime
from nl2sql.llm.failures import provider_error
from nl2sql.llm.wires import structured
from .prompts import ANSWERABILITY_PROMPT
from .schemas import AnswerabilityResponse, DatasourceResolverResponse, ResolvedDatasource


logger = get_logger("datasource_resolver")


class DatasourceResolverNode:
    """Picks the datasources a question goes to, and refuses what none can answer.

    In order:

    1. Candidates. An explicit ``datasource_id`` is used as given. With exactly
       one datasource registered, that one is used without a vector search.
       Otherwise routing (``Retriever.route``) ranks the datasources the
       caller's scope reaches, and only those: a datasource the role may not
       read is never searched, and a table it may not read (or a verified
       query that reads one) never votes or reaches the trace. ``settings.routing.candidates`` and
       ``floor_ratio`` bound what it returns.
    2. The role check: candidates the caller's role may not read are dropped;
       none left is a ``SECURITY_VIOLATION``, before any model call.
    3. The answerability check: one small structured LLM call over the allowed
       candidates' descriptions and table lists. An empty verdict ends the run
       with ``QUESTION_NOT_ANSWERABLE`` before the decomposer. After routing
       only the datasources the verdict names (and the role may read) go
       forward, at most ``settings.routing.max_datasources`` of them in fused
       order, each with its description and a table list for the decomposer
       capped at ``settings.retrieval.decomposer_tables``.

    Each datasource's schema version and snapshot are its live build's, read
    through the run's pinned index pointers (``ctx.vector_store``), never the
    state store's newest snapshot.
    """

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.policy_engine = ctx.policy_engine
        self.ds_registry = ctx.ds_registry
        self.hooks = hooks_of(ctx)
        self.routing = ctx.settings.routing
        self.curated = getattr(ctx, "curated", None)
        self.decomposer_tables = ctx.settings.retrieval.decomposer_tables
        # Registered as agent "datasourceresolver"; falls back to "default".
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.answerability_chain = ANSWERABILITY_PROMPT | structured(self.llm, AnswerabilityResponse)

    def _index_is_empty(self) -> bool:
        is_empty = getattr(self.vector_store, "is_empty", None)
        try:
            return bool(is_empty()) if callable(is_empty) else False
        except Exception:
            return False

    def _get_unsupported_datasources(self, datasource_ids: list[str]) -> list[str]:
        available_ds_ids = self.ds_registry.list_ids()
        unsupported = [ds_id for ds_id in datasource_ids if ds_id not in available_ds_ids]
        return sorted(unsupported)

    def _error_response(
        self,
        resolved_datasources: list[ResolvedDatasource],
        allowed_ids: list[str],
        unsupported_ids: list[str],
        message: str,
        severity: ErrorSeverity,
        error_code: ErrorCode,
    ) -> Dict[str, Any]:
        return {
            "datasource_resolver_response": DatasourceResolverResponse(
                resolved_datasources=resolved_datasources,
                allowed_datasource_ids=allowed_ids,
                unsupported_datasource_ids=unsupported_ids,
            ),
            "errors": [
                PipelineError(
                    node=self.node_name,
                    message=message,
                    severity=severity,
                    error_code=error_code,
                )
            ],
        }

    def _get_allowed_datasource_ids(
        self,
        user_context: UserContext,
        candidate_ids: list[str],
    ) -> list[str]:
        return [ds_id for ds_id in candidate_ids
                if self.policy_engine.scope(user_context, ds_id).reaches_datasource()]


    def _direct_candidate(self, datasource_id: str) -> ResolvedDatasource:
        """A datasource chosen without a search: the override, or the only one."""
        return ResolvedDatasource(
            datasource_id=datasource_id,
            metadata={},
            schema_version=self.vector_store.schema_version(datasource_id),
        )

    def _route_filter(self, user_context: UserContext,
                      datasource_ids: list[str]) -> Callable[[Mapping[str, Any]], bool]:
        """Which index entries may vote in routing: none that names a table the caller's scope hides.

        A table or column entry votes only when its table is one of the
        datasource's visible tables in the live snapshot (none without a
        snapshot); a verified query only when every table its SQL reads is
        visible (not when the SQL does not parse). Description and example
        entries name no table. The check is on the (datasource, table) pair:
        two datasources can hold a table of the same name.
        """
        visible: Dict[str, set] = {}
        denied_queries: Dict[str, set] = {}
        for ds_id in datasource_ids:
            scope = self.policy_engine.scope(user_context, ds_id)
            snapshot = self.vector_store.snapshot(ds_id)
            visible[ds_id] = ({TableRef(schema_name=t.table.schema_name, table_name=t.table.table_name).full_name
                               for t in snapshot.contract.tables.values()
                               if scope.allows(t.table.schema_name, t.table.table_name)} if snapshot else set())
            section = self.curated.datasources.get(ds_id) if self.curated else None
            denied_queries[ds_id] = {q.id for q in (section.verified_queries if section else [])
                                     if not self._query_allowed(scope, ds_id, q.sql)}

        def allows(metadata: Mapping[str, Any]) -> bool:
            ds_id = metadata.get("datasource_id")
            if metadata.get("type") == CURATED_TYPE:
                return metadata.get("query_id") not in denied_queries.get(ds_id, set())
            table = metadata.get("table")
            return table is None or table in visible.get(ds_id, set())

        return allows

    def _query_allowed(self, scope, datasource_id: str, sql: str) -> bool:
        try:
            parsed = inspect_verified_sql(sql, self.ds_registry.get_dialect(datasource_id))
        except Exception:
            return False
        return all(scope.allows(schema, table) for schema, table in parsed.tables)

    def _summary(self, user_context: UserContext, datasource_id: str) -> tuple[str, list[str]]:
        """The datasource's description and the sorted, bare names of the tables the caller may read.

        A table the caller's scope denies is never named to a model: not to
        the answerability judge, not to the decomposer.
        """
        snapshot = self.vector_store.snapshot(datasource_id)
        if not snapshot:
            return "", []
        description = (snapshot.metadata.description or "").strip()
        scope = self.policy_engine.scope(user_context, datasource_id)
        # Bare table names: routing needs the topic, not the qualified name.
        tables = sorted({t.table.table_name for t in snapshot.contract.tables.values()
                         if scope.allows(t.table.schema_name, t.table.table_name)})
        return description, tables

    def _answerability_input(self, user_context: UserContext, datasource_ids: list[str]) -> str:
        """Each datasource's description and table list, serialized deterministically.

        Sorted by id, keys sorted, tables sorted: the same datasources give the
        same bytes whatever order the search returned them in, so this block
        is a stable, cacheable prompt prefix.
        """
        entries = []
        for ds_id in sorted(datasource_ids):
            description, tables = self._summary(user_context, ds_id)
            entries.append({"description": description, "id": ds_id, "tables": tables})
        return json.dumps(entries, sort_keys=True, ensure_ascii=False)

    def _with_context(self, user_context: UserContext, datasource: ResolvedDatasource) -> ResolvedDatasource:
        """The datasource with its description and a capped table list, for the decomposer."""
        description, tables = self._summary(user_context, datasource.datasource_id)
        return datasource.model_copy(update={
            "description": description,
            "tables": tables[:self.decomposer_tables],
            "more_tables": max(0, len(tables) - self.decomposer_tables),
        })

    def _judge_answerability(self, user_context: UserContext, question: str,
                             datasource_ids: list[str]) -> AnswerabilityResponse:
        return self.answerability_chain.invoke(
            {"datasources": self._answerability_input(user_context, datasource_ids), "user_query": question}
        )

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        searches: list[Dict[str, Any]] = []
        out = self._resolve(state, searches)
        out = self._after_hooks(state, out)
        out["retrieval"] = self._retrieval(state, searches)
        return out

    def _after_hooks(self, state: GraphState, out: Dict[str, Any]) -> Dict[str, Any]:
        """``datasources.after`` on the datasources chosen: a hook may remove some, or end the run.

        What a hook returns is checked against the caller's scope again, so a
        hook can never route to a datasource the role may not read.
        """
        response = out.get("datasource_resolver_response")
        if out.get("errors") or response is None or not response.allowed_datasource_ids:
            return out
        verdict = self.hooks.call("datasources.after", trace_id=state.trace_id, question=state.user_query,
                                  user=state.user_context, datasource_id=state.datasource_id,
                                  datasource_ids=list(response.allowed_datasource_ids))
        if not verdict.allowed:
            return {**out, "datasource_resolver_response": response.model_copy(update={"allowed_datasource_ids": []}),
                    "errors": [verdict.error(self.node_name)]}
        kept = self._get_allowed_datasource_ids(state.user_context, list(verdict.ctx.datasource_ids))
        if kept == list(response.allowed_datasource_ids):
            return out
        if not kept:
            return {**out, **self._error_response(
                resolved_datasources=list(response.resolved_datasources), allowed_ids=[],
                unsupported_ids=list(response.unsupported_datasource_ids),
                message="The datasources.after hooks left no datasource this role may read.",
                severity=ErrorSeverity.CRITICAL, error_code=ErrorCode.SECURITY_VIOLATION)}
        return {**out, "datasource_resolver_response": response.model_copy(update={
            "resolved_datasources": [d for d in response.resolved_datasources if d.datasource_id in kept],
            "allowed_datasource_ids": kept,
        })}

    def _retrieval(self, state: GraphState, searches: list[Dict[str, Any]]) -> Dict[str, Any]:
        """What the vector search retrieved, for the run trace, or why it did not run.

        Not a state field: LangGraph drops the key, the trace keeps the node's
        whole return. It names datasources and entry ids, never entry text.
        """
        if searches:
            return {"skipped": False, "query": state.user_query, "searches": searches}
        if state.datasource_id:
            return {"skipped": True, "reason": "An explicit datasource_id was given, so no search ran."}
        try:
            single = len(self.ds_registry.list_ids()) == 1
        except Exception:
            single = False
        if single:
            return {"skipped": True, "reason": "single datasource: vector search skipped"}
        if not self.vector_store:
            return {"skipped": True, "reason": "No vector store is configured."}
        return {"skipped": False, "query": state.user_query, "searches": []}

    def _resolve(self, state: GraphState, searches: list[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            registered_ids = self.ds_registry.list_ids()
            if state.datasource_id:
                unsupported_ids = self._get_unsupported_datasources([state.datasource_id])
                if unsupported_ids:
                    unavailable = getattr(self.ds_registry, "unavailable", dict)()
                    reason = unavailable.get(state.datasource_id)
                    message = (
                        f"Datasource '{state.datasource_id}' is unavailable: {reason}"
                        if reason is not None
                        else f"Datasource not found: {state.datasource_id}."
                    )
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=unsupported_ids,
                        message=message,
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_STATE,
                    )
                candidates = [self._direct_candidate(state.datasource_id)]
                searched = False
                how = "Using explicit datasource override."
            elif len(registered_ids) == 1:
                # Searching one document for every question can only ever
                # return it, and fails when the vector store is missing.
                candidates = [self._direct_candidate(registered_ids[0])]
                searched = False
                how = "One datasource is registered, so it is used without a vector search."
            else:
                if not self.vector_store:
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=[],
                        message=(
                            "Vector store unavailable, so no datasource can be matched "
                            "among the registered ones."
                        ),
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                # The scope filter comes before the search: a datasource the
                # caller may not read is never searched, so it never ranks.
                reachable = self._get_allowed_datasource_ids(state.user_context, registered_ids)
                if not reachable:
                    _publish_refusals(self.policy_engine, state.user_context, registered_ids)
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=[],
                        message="No allowed datasources.",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SECURITY_VIOLATION,
                    )
                routed = self.vector_store.route(
                    state.user_query, reachable, explain=searches,
                    limit=self.routing.candidates, floor_ratio=self.routing.floor_ratio,
                    allows=self._route_filter(state.user_context, reachable),
                )
                candidates = [self._direct_candidate(c.datasource_id) for c in routed]
                if not candidates:
                    message = "No datasource candidates resolved."
                    if self._index_is_empty():
                        # Said plainly, because an empty index fails every question
                        # while the rest of the system (the schema snapshot, the
                        # playground's schema panel) looks healthy.
                        message = (
                            "The vector index is empty, so no datasource can be matched. "
                            "Re-index with `nl2sql index` (in the playground, use Rebuild)."
                        )
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=[],
                        message=message,
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                searched = True
                how = "Ranked by routing: five signals fused with reciprocal rank fusion."

            candidate_ids = [c.datasource_id for c in candidates]
            unsupported_ids = self._get_unsupported_datasources(candidate_ids)

            # The role check comes before the model call: a forbidden question
            # gets the RBAC refusal, and the model never sees a datasource the
            # role may not read.
            allowed_ids = self._get_allowed_datasource_ids(state.user_context, candidate_ids)
            if not allowed_ids:
                _publish_refusals(self.policy_engine, state.user_context, candidate_ids)
                return self._error_response(
                    resolved_datasources=candidates,
                    allowed_ids=[],
                    unsupported_ids=unsupported_ids,
                    message="Datasource not allowed." if len(candidates) == 1 else "No allowed datasources.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )

            verdict = self._judge_answerability(state.user_context, state.user_query, allowed_ids)
            if searched:
                # Only what the verdict accepted goes forward, and only what
                # the role may read: the verdict cannot add a datasource.
                # At most max_datasources, the best by fused score first.
                answerable = set(verdict.answerable_datasource_ids)
                forward_ids = [ds_id for ds_id in allowed_ids
                               if ds_id in answerable][:self.routing.max_datasources]
            else:
                # One candidate, chosen without a search: a non-empty verdict
                # accepts it.
                forward_ids = allowed_ids if verdict.answerable_datasource_ids else []
            if not forward_ids:
                response = self._error_response(
                    resolved_datasources=candidates,
                    allowed_ids=[],
                    unsupported_ids=unsupported_ids,
                    message=(
                        "This question can't be answered from the connected data "
                        f"({', '.join(sorted(allowed_ids))}). Ask about what that data holds."
                    ),
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.QUESTION_NOT_ANSWERABLE,
                )
                response["reasoning"] = [{"node": self.node_name, "content": f"Not answerable: {verdict.reason}"}]
                return response

            # Sorted by id: the decomposer prints these into its prompt, so the
            # same datasources give the same prompt whatever their fused order.
            forwarded = sorted(
                (self._with_context(state.user_context, c) for c in candidates if c.datasource_id in forward_ids),
                key=lambda c: c.datasource_id,
            )
            return {
                "datasource_resolver_response": DatasourceResolverResponse(
                    resolved_datasources=forwarded,
                    allowed_datasource_ids=sorted(forward_ids),
                    unsupported_datasource_ids=unsupported_ids,
                ),
                "reasoning": [
                    {"node": self.node_name, "content": how},
                    {"node": self.node_name, "content": f"Answerable: {verdict.reason}"},
                ],
            }
        except Exception as exc:
            logger.error(f"Datasource resolver failed: {exc}")
            return {
                "datasource_resolver_response": DatasourceResolverResponse(),
                "errors": [
                    provider_error(self.node_name, exc) or PipelineError(
                        node=self.node_name,
                        message=f"Datasource resolution failed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                ],
            }


def _publish_refusals(policy_engine, user_context: UserContext, datasource_ids: list[str]) -> None:
    """``policy.decision`` for each datasource the role check refused, when it refused them all (D16).

    Before a search that means every registered datasource; otherwise the
    explicit or only one. Routing that skips some while others go on is
    filtering and publishes nothing.
    """
    for ds_id in datasource_ids:
        rule = policy_engine.scope(user_context, ds_id).datasource_rule()
        if rule is not None:
            publish("policy.decision", {"datasource_id": ds_id, "rule": rule, "step": "datasource_resolver"})
