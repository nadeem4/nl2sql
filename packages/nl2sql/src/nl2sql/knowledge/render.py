"""The ``[BUSINESS_CONTEXT]`` block the planner and the refiner send (docs/architecture/wave_b.md, section 4).

Knowledge reaches a prompt only as JSON values (D39): one object, keys
sorted, no whitespace, ``ensure_ascii=False``, so a newline or a block marker
in a definition stays inside one JSON string on one line. ``notes`` and
synonyms are never in it. ``fit_business_context`` cuts the block to
``settings.knowledge.block_max_chars``: the lowest-scored terms go first,
then rules from the last; what it cut is recorded on ``KnowledgeUsed.dropped``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Tuple

from nl2sql.knowledge.models import RuleUse, TermMatch


def _term(term: TermMatch) -> Dict[str, Any]:
    row = {"term": term.term, "definition": term.definition, "columns": term.columns, "tables": term.tables}
    return {k: v for k, v in row.items() if v not in (None, "", [])}


def render_business_context(terms: Sequence[TermMatch], rules: Sequence[RuleUse]) -> str:
    """The block's JSON: ``{"rules": [{"id", "text"}], "terms": [{"columns", "definition", "tables", "term"}]}``.

    An empty list is left out, so no knowledge renders ``{}``.
    """
    block: Dict[str, Any] = {}
    if rules:
        block["rules"] = [{"id": rule.id, "text": rule.text} for rule in rules]
    if terms:
        block["terms"] = [_term(term) for term in terms]
    return json.dumps(block, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def business_context_of(state: Any) -> str:
    """A sub-query's ``[BUSINESS_CONTEXT]``: the terms and rules the schema retriever picked (``knowledge_used``)."""
    used = getattr(state, "knowledge_used", None)
    return render_business_context(used.terms, used.rules) if used is not None else render_business_context([], [])


def fit_business_context(terms: Sequence[TermMatch], rules: Sequence[RuleUse], *, max_chars: int
                         ) -> Tuple[List[TermMatch], List[RuleUse], List[str]]:
    """``terms`` and ``rules`` cut until the block is at most ``max_chars``, and what was cut (names and ids).

    Terms go first, lowest score first (the last of equal scores first); then
    rules, from the last.
    """
    kept_terms = sorted(terms, key=lambda t: -t.score)
    kept_rules = list(rules)
    dropped: List[str] = []
    while (kept_terms or kept_rules) and len(render_business_context(kept_terms, kept_rules)) > max_chars:
        if kept_terms:
            dropped.append(kept_terms.pop().term)
        else:
            dropped.append(kept_rules.pop().id)
    return kept_terms, kept_rules, dropped
