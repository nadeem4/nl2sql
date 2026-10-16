"""Render a run's explanation from its typed plans and state, with no model call (docs/architecture/wave_b.md, D51).

``explain_plan`` renders one plan (an executed sub-query's, or a
clarification option's); ``explain_state`` renders a finished run's state,
one step per sub-query, which is what ``result_from_state`` puts on
``QueryResult.explanation``. A step reads only the plan, the sub-query's
output (``knowledge_used``, the readings not chosen, the row cap), the run's
warnings and, when given, the datasource's glossary: the same input always
gives the same words. The plan's own ``reasoning`` is model text and appears
only as ``planner_note``.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from nl2sql.explain.models import ExplainedStep, Explanation, TermUse
from nl2sql.explain.phrases import Names, conjuncts, is_aggregate, listed, phrase, words
from nl2sql.knowledge.models import KnowledgeUsed
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, JoinSpec, PlanModel
from nl2sql_sdk.knowledge import DatasourceKnowledge

# What the sanity checks report about a result the run kept (Wave A); RESULT_TRUNCATED is the row cap's.
_SANITY_CODES = ("VALUE_NOT_FOUND", "JOIN_FAN_OUT", "ALL_NULL_COLUMN", "JOIN_KEY_UNCONVERTED")
_SOURCES = {"llm": "generated"}
_JOIN_TYPES = {
    "inner": "each {left} to its {right}",
    "left": "each {left} with its {right}, if any",
    "right": "each {right} with its {left}, if any",
    "full": "{left} and {right}, matched where they can be",
}
_COMBINED = {
    "join": "The results of {inputs} were joined on {keys}.",
    "compare": "The results of {inputs} were compared on {keys}.",
    "union": "The results of {inputs} were put together in one list.",
}


def _get(source: Any, name: str, default: Any = None) -> Any:
    """``name`` off a mapping or a model: LangGraph hands back either."""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------- the plan's parts

def _names(plan: PlanModel, knowledge: Optional[DatasourceKnowledge]) -> Names:
    # A select item's alias names it where the plan refers to it without a table alias (ORDER BY revenue).
    return Names({t.alias: t.name for t in plan.tables}, {t.alias: t.schema_name for t in plan.tables}, knowledge,
                 {item.alias: " ".join(words(item.alias)) for item in plan.select_items if item.alias})


def _tables(plan: PlanModel, names: Names) -> List[str]:
    seen: Dict[str, str] = {}
    for table in plan.tables:
        qualified = names.qualified(table.alias)
        if qualified.lower() in seen:
            continue
        described = names.description(table.alias)
        seen[qualified.lower()] = f"{qualified}: {described} (glossary)" if described else qualified
    return list(seen.values())


def _column_pairs(join: JoinSpec) -> Tuple[List[Tuple[str, str, str, str]], List[Expr]]:
    """The join's column equalities between its two tables, as (left alias, column, right alias, column), and the rest."""
    pairs, rest = [], []
    for part in conjuncts(join.condition):
        if (part.kind == "binary" and part.op == "=" and part.left.kind == "column" and part.right.kind == "column"
                and {part.left.alias, part.right.alias} == {join.left_alias, join.right_alias}):
            left, right = (part.left, part.right) if part.left.alias == join.left_alias else (part.right, part.left)
            pairs.append((left.alias, left.column_name, right.alias, right.column_name))
        else:
            rest.append(part)
    return pairs, rest


def _sides(names: Any) -> List[Tuple[str, str]]:
    """A glossary join side as (table, column) pairs, lower-cased, the schema dropped."""
    out = []
    for name in [names] if isinstance(names, str) else list(names):
        parts = name.lower().split(".")
        out.append((parts[-2] if len(parts) > 1 else "", parts[-1]))
    return out


def _glossary_pairs(knowledge: Optional[DatasourceKnowledge]) -> List[Set[frozenset]]:
    joins = []
    for join in (knowledge.joins if knowledge else []):
        joins.append({frozenset((a, b)) for a, b in zip(_sides(join.from_), _sides(join.to))})
    return joins


def _joins(plan: PlanModel, names: Names, knowledge: Optional[DatasourceKnowledge]) -> List[str]:
    glossary = _glossary_pairs(knowledge)
    out = []
    for join in plan.joins:
        pairs, rest = _column_pairs(join)
        text = _JOIN_TYPES[join.join_type].format(left=names.table(join.left_alias),
                                                  right=names.table(join.right_alias))
        if pairs:
            keys = [lc if lc.lower() == rc.lower() else f"{names.tables.get(la, la)}.{lc} = "
                    f"{names.tables.get(ra, ra)}.{rc}" for la, lc, ra, rc in pairs]
            used = {frozenset(((names.tables.get(la, "").lower(), lc.lower()), (names.tables.get(ra, "").lower(),
                                                                                 rc.lower())))
                    for la, lc, ra, rc in pairs}
            source = "glossary" if any(used <= g for g in glossary) else "declared key"
            text += f", on {listed(keys)} ({source})"
        if rest:
            text += f", where {listed([phrase(p, names) for p in rest])}"
        out.append(text)
    return out


def _order(plan: PlanModel, names: Names) -> Optional[str]:
    named = {item.expr.model_dump_json(): " ".join(words(item.alias)) for item in plan.select_items if item.alias}
    parts = []
    for item in plan.order_by:
        target = named.get(item.expr.model_dump_json()) or phrase(item.expr, names)
        parts.append(f"{'highest' if item.direction == 'desc' else 'lowest'} {target} first")
    text = ", then ".join(parts)
    if plan.limit is not None:
        text = f"{text}, top {plan.limit}" if text else f"the first {plan.limit} rows"
    if plan.offset:
        text = f"{text}, after skipping {plan.offset}" if text else f"after skipping {plan.offset} rows"
    return text or None


def _columns_read(plan: PlanModel) -> Set[str]:
    """Every ``table.column`` the plan reads, lower-cased, with and without the schema."""
    aliases = {t.alias: t for t in plan.tables}
    found: Set[str] = set()

    def walk(expr: Optional[Expr]) -> None:
        if expr is None:
            return
        if expr.kind == "column" and expr.column_name:
            tables = [aliases[expr.alias]] if expr.alias in aliases else list(aliases.values())
            for table in tables:
                found.add(f"{table.name}.{expr.column_name}".lower())
                if table.schema_name:
                    found.add(f"{table.schema_name}.{table.name}.{expr.column_name}".lower())
        for child in [expr.left, expr.right, expr.expr, expr.else_expr, *expr.args]:
            walk(child)
        for when in expr.whens:
            walk(when.condition)
            walk(when.result)

    for item in [*plan.select_items, *plan.group_by, *plan.order_by]:
        walk(item.expr)
    for join in plan.joins:
        walk(join.condition)
    walk(plan.where)
    walk(plan.having)
    return found


def _terms(plan: PlanModel, used: Optional[KnowledgeUsed]) -> List[TermUse]:
    if used is None:
        return []
    read = _columns_read(plan)
    return [TermUse(term=t.term, definition=t.definition or "", source=t.source)
            for t in used.terms if any(c.lower() in read for c in t.columns)]


def _text(plan: PlanModel, names: Names, step: ExplainedStep) -> str:
    shown = [" ".join(words(i.alias)) if i.alias and is_aggregate(i.expr) else phrase(i.expr, names)
             for i in plan.select_items]
    first = f"Shows {'each different ' if plan.distinct else ''}{listed(shown) or 'rows'}"
    if plan.group_by:
        first += f", by {listed([phrase(g.expr, names) for g in plan.group_by])}"
    reads = f"Reads {listed([names.table(t.alias) for t in _unique_tables(plan)])}"
    where = [phrase(p, names) for p in conjuncts(plan.where)]
    if where:
        reads += f", where {listed(where)}"
    sentences = [first + ".", reads + "."]
    having = [phrase(p, names) for p in conjuncts(plan.having)]
    if having:
        sentences.append(f"Keeps the groups where {listed(having)}.")
    if step.order:
        sentences.append(_cap(step.order) + ".")
    return " ".join(sentences)


def _unique_tables(plan: PlanModel) -> list:
    seen, out = set(), []
    for table in plan.tables:
        key = (table.schema_name or "", table.name.lower(), table.alias if _repeats(plan, table.name) else "")
        if key not in seen:
            seen.add(key)
            out.append(table)
    return out


def _repeats(plan: PlanModel, name: str) -> bool:
    return sum(1 for t in plan.tables if t.name.lower() == name.lower()) > 1


# ---------------------------------------------------------------- assumptions

def _reading_not_chosen(phrase_text: Optional[str], labels: Sequence[str], terms: Sequence[TermUse]) -> List[str]:
    if not phrase_text or not labels:
        return []
    read_as = f"read as {terms[0].term}" if len(terms) == 1 else "read one way"
    return [f"'{phrase_text}' was {read_as}; it could also mean {', or '.join(labels)}."]


def _from_warnings(warnings: Iterable[Any], sub_query_id: Optional[str]) -> List[str]:
    out = []
    for warning in warnings:
        if sub_query_id is None or _get(warning, "sub_query_id") != sub_query_id:
            continue
        code, message = _get(warning, "code"), _get(warning, "message") or ""
        recovered = bool((_get(warning, "detail") or {}).get("recovered"))
        if code == "VALUE_NOT_FOUND" and recovered:
            out.append(f"A filter value was not in the data, so the closest existing one was used: {message}")
        elif code in _SANITY_CODES and not recovered:
            out.append(message)
    return out


def _truncation(output: Any) -> List[str]:
    row_cap = _get(output, "row_cap")
    if _get(output, "truncated") and row_cap:
        return [f"Only the first {row_cap} rows were read; the full result may have more."]
    return []


# ---------------------------------------------------------------- public

def explain_plan(
    plan: PlanModel,
    *,
    datasource_id: str,
    sub_query_id: Optional[str] = None,
    source: str = "generated",
    knowledge_used: Optional[KnowledgeUsed] = None,
    knowledge: Optional[DatasourceKnowledge] = None,
    assumptions: Sequence[str] = (),
) -> ExplainedStep:
    """One plan in plain English.

    ``knowledge_used`` is what the step's prompt carried (its terms and rules
    are listed); ``knowledge`` is the datasource's glossary, which names
    tables and columns by their descriptions and tells a glossary join from a
    declared key. ``assumptions`` are appended as given.
    """
    names = _names(plan, knowledge)
    filters = [phrase(p, names) for p in conjuncts(plan.where)]
    filters += [f"for each group, {phrase(p, names)}" for p in conjuncts(plan.having)]
    measures = [(f"{' '.join(words(i.alias))}: " if i.alias else "") + phrase(i.expr, names)
                for i in plan.select_items if is_aggregate(i.expr)]
    step = ExplainedStep(
        sub_query_id=sub_query_id,
        datasource_id=datasource_id,
        source=_SOURCES.get(source, source),
        tables=_tables(plan, names),
        joins=_joins(plan, names, knowledge),
        filters=filters,
        groupings=[f"by {phrase(g.expr, names)}" for g in plan.group_by],
        measures=measures,
        order=_order(plan, names),
        terms=_terms(plan, knowledge_used),
        rules=[r.text for r in knowledge_used.rules] if knowledge_used else [],
        assumptions=list(assumptions),
        planner_note=plan.reasoning or None,
        text="",
    )
    return step.model_copy(update={"text": _text(plan, names, step)})


def explain_verified(match: Any, sub_query_id: Optional[str], assumptions: Sequence[str] = ()) -> ExplainedStep:
    """A verified query's answer: who wrote it and the values it ran with; there is no plan."""
    params = _get(match, "params") or {}
    text = f"Answered by the verified query `{_get(match, 'query_id')}`, written by the data team"
    if params:
        text += ", with " + listed([f"{k} = {params[k]}" for k in sorted(params)])
    text += "."
    description = (_get(match, "description") or "").strip()
    if description:
        text += f" {description}"
    return ExplainedStep(sub_query_id=sub_query_id, datasource_id=_get(match, "datasource_id") or "",
                         source="verified", assumptions=list(assumptions), text=text)


def _explain_output(output: Any, state: Mapping[str, Any], warnings: Sequence[Any],
                    knowledge: Optional[Mapping[str, DatasourceKnowledge]]) -> Optional[ExplainedStep]:
    sub_query = _get(output, "sub_query")
    sub_query_id = _get(sub_query, "id")
    datasource_id = _get(sub_query, "datasource_id") or ""
    found = _from_warnings(warnings, sub_query_id) + _truncation(output)
    source = _get(output, "plan_source") or "llm"
    match = state.get("verified_match")
    if source == "verified" and match is not None:
        return explain_verified(match, sub_query_id, found)
    plan = _get(output, "plan")
    if plan is None:
        return None
    plan = plan if isinstance(plan, PlanModel) else PlanModel.model_validate(plan)
    used = _get(output, "knowledge_used")
    if isinstance(used, dict):
        used = KnowledgeUsed.model_validate(used)
    terms = _terms(plan, used)
    labels = [_get(a, "label") for a in _get(output, "alternatives") or []]
    readings = _reading_not_chosen(_get(output, "ambiguous_phrase"), labels, terms)
    return explain_plan(plan, datasource_id=datasource_id, sub_query_id=sub_query_id, source=source,
                        knowledge_used=used, knowledge=(knowledge or {}).get(datasource_id),
                        assumptions=readings + found)


def _combined(state: Mapping[str, Any], steps: List[ExplainedStep]) -> Optional[str]:
    if len(steps) < 2:
        return None
    texts = []
    for group in _get(state.get("decomposer_response"), "combine_groups") or []:
        operation = _get(group, "operation")
        if operation not in _COMBINED:
            continue
        inputs = listed([_get(i, "subquery_id") for i in _get(group, "inputs") or []])
        keys = listed([_get(k, "left") if _get(k, "left") == _get(k, "right") else
                       f"{_get(k, 'left')} = {_get(k, 'right')}" for k in _get(group, "join_keys") or []])
        texts.append(_COMBINED[operation].format(inputs=inputs, keys=keys))
    return " ".join(texts) or "Each step was answered on its own."


def explain_state(
    state: Mapping[str, Any],
    warnings: Optional[Sequence[Any]] = None,
    knowledge: Optional[Mapping[str, DatasourceKnowledge]] = None,
) -> Optional[Explanation]:
    """A finished run's explanation, one step per sub-query with a plan (or a verified answer); None without one.

    Steps follow the decomposition's order. ``warnings`` are the run's
    (``QueryResult.warnings``; the state's own when not given); ``knowledge``
    maps a datasource id to its glossary.
    """
    warnings = list(state.get("warnings") or []) if warnings is None else list(warnings)
    order = {_get(sq, "id"): n for n, sq in enumerate(_get(state.get("decomposer_response"), "sub_queries") or [])}
    outputs = sorted((state.get("subgraph_outputs") or {}).values(),
                     key=lambda o: (order.get(_get(_get(o, "sub_query"), "id"), len(order)),
                                    _get(_get(o, "sub_query"), "id") or ""))
    steps = [step for output in outputs if _get(output, "sub_query")
             for step in [_explain_output(output, state, warnings, knowledge)] if step is not None]
    if not steps:
        return None
    return Explanation(steps=steps, combined=_combined(state, steps))
