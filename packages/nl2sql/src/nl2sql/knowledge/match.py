"""Which knowledge a sub-query touches: matched terms and the rules of its tables (docs/architecture/wave_b.md, section 4).

A term matches lexically: its name or one of its synonyms appears, as whole
words, in the sub-query's intent or the question, case and punctuation
ignored. A name scores 1.0, a synonym 0.9; the best ``top_k`` are kept, and
every matching term is kept up to that, so two terms sharing a synonym are
both sent (that is how the planner sees the ambiguity). A term whose
``knowledge.term`` index entry is at least ``floor`` similar to the intent
matches too, scoring that similarity (K3).

A rule is sent when its ``tables`` is empty or names one of the tables sent
with the sub-query. Both take knowledge already filtered to the caller's
scope (``nl2sql.knowledge.scope.visible``).
"""
from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Optional, Sequence, Set

from nl2sql_sdk.knowledge import Rule, Term
from nl2sql_sdk.schema import SchemaSnapshot

from nl2sql.knowledge.models import RuleUse, TermMatch
from nl2sql.knowledge.resolve import resolve_table

NAME_SCORE = 1.0
SYNONYM_SCORE = 0.9

_NOT_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """Case-folded words separated by single spaces: punctuation and underscores are spaces."""
    return " ".join(_NOT_WORD.sub(" ", (text or "").casefold()).split())


def _appears(phrase: str, texts: Sequence[str]) -> bool:
    wanted = normalize(phrase)
    return bool(wanted) and any(f" {wanted} " in f" {text} " for text in texts)


def match_terms(terms: Iterable[Term], texts: Iterable[str], *, sources: Mapping[str, str],
                top_k: int, similar: Optional[Mapping[str, float]] = None,
                floor: float = 0.6) -> List[TermMatch]:
    """The terms matching ``texts``, best first (score, then name), at most ``top_k``.

    A term matches when its name or a synonym appears in ``texts``, or when
    ``similar`` (a term's name to the similarity of its index entry to the
    intent) reaches ``floor``. Only the ``terms`` given can match, so the
    caller passes the ones its scope shows. ``sources`` maps a term's name to
    the knowledge source it came from.
    """
    similar = similar or {}
    normalized = [normalize(text) for text in texts if text]
    found: List[TermMatch] = []
    for term in terms:
        if _appears(term.term, normalized):
            score = NAME_SCORE
        elif any(_appears(synonym, normalized) for synonym in term.synonyms):
            score = SYNONYM_SCORE
        elif similar.get(term.term, 0.0) >= floor:
            score = similar[term.term]
        else:
            continue
        found.append(TermMatch(term=term.term, definition=term.definition, columns=list(term.columns),
                               tables=list(term.tables), source=sources.get(term.term, "knowledge"), score=score))
    found.sort(key=lambda m: (-m.score, m.term.casefold()))
    return found[:top_k]


def rules_for(rules: Iterable[Rule], tables: Set[str], snapshot: SchemaSnapshot, *,
              sources: Mapping[str, str]) -> List[RuleUse]:
    """The rules for a sub-query whose tables are ``tables`` (bare names, case ignored), in glossary order.

    A rule with no ``tables`` concerns every sub-query on its datasource.
    """
    sent = {name.casefold() for name in tables}

    def concerns(rule: Rule) -> bool:
        if not rule.tables:
            return True
        for name in rule.tables:
            table, _ = resolve_table(name, snapshot)
            if table is not None and table.table.table_name.casefold() in sent:
                return True
        return False

    return [RuleUse(id=rule.id, text=rule.text, tables=list(rule.tables), source=sources.get(rule.id, "knowledge"))
            for rule in rules if concerns(rule)]
