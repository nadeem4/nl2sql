"""The curated matcher: answer a question with a verified query when one fits.

The graph's entry point when curated answers are configured. It searches the
verified queries of the datasources the caller may read, asks the model once
to pick one and fill its parameters, checks the values and the caller's access
in code, and runs the saved SQL with bound parameters. On a hit it fills the
state the way a one-sub-query generated run leaves it, so the aggregator and
the answer synthesizer run unchanged. Anything else falls back: the node
returns only its trace record and the run continues at the datasource resolver
as if the matcher were not there.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from nl2sql.auth.rbac import default_schema, table_allowed
from nl2sql.common.logger import get_logger
from nl2sql.config.curated import CuratedSqlError, VerifiedQuery, inspect_verified_sql
from nl2sql.runtime import Runtime
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor import SqlExecutorService
from nl2sql.llm.wires import structured
from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql_sdk.capabilities import DatasourceCapability

from .params import ParamError, resolve_params
from .prompts import CURATED_MATCHER_PROMPT
from .schemas import NONE_OF_THESE, VerifiedMatch, VerifiedQueryChoice

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

logger = get_logger("curated_matcher")

# The graph node's name, which is also what its sub-query output is keyed by.
NODE = "curated_matcher"


class _Fallback(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def curated_answers_on(ctx: Any) -> bool:
    """Whether a run should try verified queries first: the file is loaded and holds one."""
    curated = getattr(ctx, "curated", None)
    return bool(curated and any(ds.verified_queries for ds in curated.datasources.values()))


class CuratedMatcherNode:
    """Matches a question to a verified query and runs it, or falls back.

    Every fallback carries one reason code in the node's ``curated_match``
    record: ``not_configured``, ``no_candidates``, ``below_floor``,
    ``none_of_these``, ``model_failed``, ``unknown_query``, ``param_invalid``,
    ``param_missing``, ``policy_denied``, ``capability_missing``,
    ``execution_failed``. A fallback is never an error of the run.
    """

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.curated = ctx.curated
        self.vector_store = ctx.vector_store
        self.rbac = ctx.rbac
        self.ds_registry = ctx.ds_registry
        self.schema_store = ctx.schema_store
        self.tenant_id = ctx.tenant_id
        self.row_limit = ctx.settings.results.row_limit
        self.match = ctx.settings.curated
        # Registered as agent "curatedmatcher"; falls back to "default".
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.chain = CURATED_MATCHER_PROMPT | structured(self.llm, VerifiedQueryChoice)
        self.executor = SqlExecutorService(ctx.ds_registry, ctx.artifact_store,
                                          fetch_batch_size=ctx.settings.results.fetch_batch_size)

    # -- the steps --------------------------------------------------------

    def _queries(self, ds_id: str) -> Dict[str, VerifiedQuery]:
        section = self.curated.datasources.get(ds_id) if self.curated else None
        return {q.id: q for q in section.verified_queries} if section else {}

    def _searchable(self, state: GraphState) -> List[str]:
        """Datasources with verified queries that the caller may read, the pinned one alone if pinned."""
        allowed = self.rbac.get_allowed_datasources(state.user_context) or []
        registered = list(self.ds_registry.list_ids())
        readable = registered if "*" in allowed else [d for d in registered if d in allowed]
        if state.datasource_id:
            readable = [d for d in readable if d == state.datasource_id]
        return [d for d in readable if self._queries(d)]

    def _candidates_block(self, candidates: List[Tuple[Any, VerifiedQuery]]) -> str:
        entries = []
        for cand, query in candidates:
            params = {}
            for name, spec in query.params.items():
                params[name] = {k: v for k, v in spec.model_dump().items()
                                if v is not None and k != "example"}
                params[name]["required"] = spec.default is None
            entries.append({"id": f"{cand.datasource_id}.{cand.query_id}",
                            "description": query.description, "params": params})
        return json.dumps(entries, sort_keys=True, ensure_ascii=False)

    def _chosen(self, choice: VerifiedQueryChoice,
                candidates: List[Tuple[Any, VerifiedQuery]]) -> Tuple[str, VerifiedQuery]:
        """The candidate the model named, by its qualified id (or a bare id that is unambiguous)."""
        picked = choice.query_id.strip()
        if picked == NONE_OF_THESE:
            raise _Fallback("none_of_these", choice.reason)
        matches = [(c.datasource_id, q) for c, q in candidates
                   if picked == f"{c.datasource_id}.{c.query_id}"]
        if not matches:
            matches = [(c.datasource_id, q) for c, q in candidates if picked == c.query_id]
        if len(matches) != 1:
            raise _Fallback("unknown_query", f"{picked!r} is not one of the candidates")
        return matches[0]

    def _check_access(self, state: GraphState, ds_id: str, tables: List[Tuple[str, str]]) -> None:
        allowed = self.rbac.get_allowed_tables(state.user_context)
        default = default_schema(self.ds_registry, ds_id)
        for schema, table in tables:
            name = f"{schema}.{table}" if schema else table
            if not table_allowed(allowed, ds_id, table, schema, default):
                raise _Fallback("policy_denied", f"'{ds_id}.{name}' is not allowed for this role")

    def _row_ceiling(self, ds_id: str) -> int:
        ceiling = self.row_limit
        adapter = self.ds_registry.get_adapter(ds_id)
        return min(int(getattr(adapter, "row_limit", None) or ceiling), ceiling)

    # -- the node ---------------------------------------------------------

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        record: Dict[str, Any] = {"matched": False, "reason": None, "floor": self.match.match_floor,
                                  "candidates": [], "choice": None, "params": None}
        searches: List[Dict[str, Any]] = []
        try:
            out = self._match(state, record, searches)
            record["matched"] = True
        except _Fallback as fallback:
            record["reason"] = fallback.reason
            record["detail"] = fallback.detail
            out = {}
            if fallback.reason == "execution_failed":
                out["warnings"] = [{"node": self.node_name, "message": fallback.detail,
                                    "error_code": "CURATED_FALLBACK", "severity": "WARNING"}]
            logger.info(f"Curated answers fell back ({fallback.reason}): {fallback.detail}")
        except Exception as exc:  # never an error of the run
            logger.warning(f"Curated matcher failed, falling back: {exc}")
            record["reason"], record["detail"] = "execution_failed", f"Matcher crashed: {exc}"
            out = {}
        out["curated_match"] = record
        if searches:
            out["retrieval"] = {"skipped": False, "query": state.user_query, "searches": searches}
        return out

    def _match(self, state: GraphState, record: Dict[str, Any],
               searches: List[Dict[str, Any]]) -> Dict[str, Any]:
        # 1. Candidates, from the datasources this caller may read only.
        datasource_ids = self._searchable(state)
        if not datasource_ids:
            raise _Fallback("not_configured", "No readable datasource has verified queries.")
        found = self.vector_store.retrieve_verified_query_candidates(
            state.user_query, datasource_ids, k=self.match.match_top_k, explain=searches)
        # The file, not the index, is the truth: a query that has since been
        # removed is skipped.
        candidates = [(c, self._queries(c.datasource_id)[c.query_id]) for c in found
                      if c.query_id in self._queries(c.datasource_id)]
        record["candidates"] = [{"datasource_id": c.datasource_id, "query_id": c.query_id,
                                 "similarity": round(c.score, 4)} for c, _ in candidates]
        if not candidates:
            raise _Fallback("no_candidates", "No verified query resembles the question.")

        # 2. The floor: below it, no model call.
        best = max(c.score for c, _ in candidates)
        if best < self.match.match_floor:
            raise _Fallback("below_floor", f"Best similarity {best:.3f} is below {self.match.match_floor}.")

        # 3. One structured-output call.
        try:
            choice: VerifiedQueryChoice = self.chain.invoke(
                {"candidates": self._candidates_block(candidates), "user_query": state.user_query})
        except Exception as exc:
            raise _Fallback("model_failed", f"The choice call failed: {exc}") from None
        record["choice"] = choice.model_dump()
        ds_id, query = self._chosen(choice, candidates)

        # 4. The values, checked by code.
        try:
            params = resolve_params(query, choice.params)
        except ParamError as exc:
            raise _Fallback(exc.reason, str(exc)) from None
        record["params"] = params

        # 5. Access to every table the SQL reads, and read-only once more.
        try:
            parsed = inspect_verified_sql(query.sql, self.ds_registry.get_dialect(ds_id))
        except CuratedSqlError as exc:
            raise _Fallback("execution_failed", f"Verified query '{ds_id}.{query.id}': {exc}") from None
        self._check_access(state, ds_id, parsed.tables)
        if query.params and not self.ds_registry.supports(ds_id, DatasourceCapability.SUPPORTS_BOUND_PARAMETERS):
            raise _Fallback("capability_missing", f"The adapter for '{ds_id}' cannot bind parameters.")

        # 6. Run it, under the row ceiling, with the values bound.
        return self._run(state, ds_id, query, params)

    def _run(self, state: GraphState, ds_id: str, query: VerifiedQuery, params: Dict[str, Any]) -> Dict[str, Any]:
        sub_query = SubQuery(
            id=f"verified_{query.id}",
            datasource_id=ds_id,
            intent=state.user_query,
            schema_version=self.schema_store.get_latest_version(ds_id),
        )
        row_cap = self._row_ceiling(ds_id)
        response = self.executor.execute(ExecutorRequest(
            node_id=sub_query.id,
            trace_id=state.trace_id,
            subgraph_name=NODE,
            datasource_id=ds_id,
            schema_version=sub_query.schema_version,
            sql=query.sql,
            parameters=params,
            max_rows=row_cap,
            user_context=state.user_context,
            tenant_id=self.tenant_id,
        ))
        artifact = response.artifact
        if response.errors or artifact is None:
            reasons = "; ".join(e.message for e in response.errors) or "no result"
            raise _Fallback("execution_failed", f"Verified query '{ds_id}.{query.id}' failed: {reasons}")

        decomposition = DecomposerResponse(
            sub_queries=[sub_query],
            combine_groups=[CombineGroup(group_id="verified", operation="standalone",
                                         inputs=[CombineInput(subquery_id=sub_query.id)])],
        )
        output = SubgraphOutput(
            sub_query=sub_query,
            subgraph_id=f"{NODE}:{sub_query.id}:{state.trace_id}",
            subgraph_name=NODE,
            plan_source="verified",
            sql_draft=query.sql.strip(),
            row_cap=row_cap,
            truncated=artifact.row_count >= row_cap,
            artifact=artifact,
            validation=[
                ValidationCheck(name="read_only", passed=True, message="One read-only query."),
                ValidationCheck(name="params", passed=True, message="Every value has its declared type and bounds."),
                ValidationCheck(name="policy", passed=True, message="Every table it reads is allowed for this role."),
            ],
            status="success",
        )
        match = VerifiedMatch(datasource_id=ds_id, query_id=query.id, description=query.description,
                              params=params)
        return {
            "verified_match": match,
            "decomposer_response": decomposition,
            "execution_dag": build_execution_dag(decomposition),
            "artifact_refs": {sub_query.id: artifact},
            "subgraph_outputs": {output.subgraph_id: output},
            "reasoning": [{"node": self.node_name,
                           "content": f"Answered by verified query '{ds_id}.{query.id}'."}],
        }
