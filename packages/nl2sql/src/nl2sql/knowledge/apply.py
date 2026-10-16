"""The glossary appliers: a proposal's ``change`` merged into a ``Glossary`` (docs/architecture/wave_b.md, section 5).

The review queue's accept calls ``apply_change`` (or ``apply_proposal``) for
each accepted ``table``, ``column``, ``join`` and ``term`` proposal, then
writes the result once with ``write_glossary``. Each is a pure function: it
returns a new, validated glossary and writes nothing.

| Kind | ``target`` | What the change does |
|---|---|---|
| ``table`` | the table's name | sets its fields over the table's knowledge |
| ``column`` | ``table.column`` | sets its fields over the column's knowledge |
| ``join`` | ``from->to`` | the join (``from``, ``to``, ...) replaces the same join, or is added |
| ``term`` | the term's name | the term replaces the one of the same name (D50), or is added |
"""
from __future__ import annotations

from typing import Any, Dict

from pydantic import ValidationError

from nl2sql_sdk.knowledge import ITEM_MAX_CHARS, DatasourceKnowledge, Glossary, JoinKnowledge, Term
from nl2sql_sdk.stores import Proposal

GLOSSARY_KINDS = ("table", "column", "join", "term")


def _same(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _join_key(join: JoinKnowledge) -> tuple:
    def side(value):
        return tuple(v.strip().lower() for v in ([value] if isinstance(value, str) else value))

    return side(join.from_), side(join.to)


def apply_change(glossary: Glossary, kind: str, datasource_id: str, target: str, change: Dict[str, Any], *,
                 item_max_chars: int = ITEM_MAX_CHARS) -> Glossary:
    """``glossary`` with one change applied to ``datasource_id``'s knowledge.

    Raises:
        ValueError: ``kind`` is not a glossary kind, or the result is not a
            valid glossary (a text over ``item_max_chars``, ``values`` on a
            column that is not an enum, ...); the message names the item.
    """
    if kind not in GLOSSARY_KINDS:
        raise ValueError(f"A {kind} proposal does not change the glossary; the glossary takes "
                         f"{', '.join(GLOSSARY_KINDS)}.")
    data = glossary.model_dump(mode="json", by_alias=True, exclude_unset=True)
    datasources = data.setdefault("datasources", {})
    block = datasources.setdefault(datasource_id, {})
    if kind in ("table", "column"):
        items = block.setdefault("tables" if kind == "table" else "columns", {})
        name = next((n for n in items if _same(n, target)), target)
        items[name] = {**items.get(name, {}), **change}
    elif kind == "join":
        try:
            new = JoinKnowledge.model_validate(change, context={"item_max_chars": item_max_chars})
        except ValidationError as e:
            raise ValueError(f"The join change to {datasource_id} {target} is not valid: {e}") from None
        block["joins"] = _replace_in_place(glossary, datasource_id, new)
    else:  # term
        try:
            new_term = Term.model_validate(change, context={"item_max_chars": item_max_chars})
        except ValidationError as e:
            raise ValueError(f"The term change to {datasource_id} {target} is not valid: {e}") from None
        terms = block.setdefault("terms", [])
        dumped = new_term.model_dump(mode="json", exclude_unset=True)
        at = next((i for i, t in enumerate(terms) if _same(t["term"], target) or _same(t["term"], new_term.term)), None)
        if at is None:
            terms.append(dumped)
        else:
            terms[at] = dumped
    try:
        return Glossary.model_validate(data, context={"item_max_chars": item_max_chars})
    except ValidationError as e:
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in e.errors())
        raise ValueError(f"The {kind} change to {datasource_id} {target} is not valid: {reasons}.") from None


def _replace_in_place(glossary: Glossary, datasource_id: str, new: JoinKnowledge) -> list:
    """The datasource's joins with ``new`` in place of the same join (so the file does not reorder), or added."""
    current = glossary.datasources.get(datasource_id, DatasourceKnowledge()).joins
    out, replaced = [], False
    for join in current:
        if _join_key(join) == _join_key(new):
            out.append(new.model_dump(mode="json", by_alias=True, exclude_unset=True))
            replaced = True
        else:
            out.append(join.model_dump(mode="json", by_alias=True, exclude_unset=True))
    if not replaced:
        out.append(new.model_dump(mode="json", by_alias=True, exclude_unset=True))
    return out


def apply_proposal(glossary: Glossary, proposal: Proposal, *, item_max_chars: int = ITEM_MAX_CHARS) -> Glossary:
    """``apply_change`` with a proposal's kind, datasource, target and change."""
    return apply_change(glossary, proposal.kind, proposal.datasource_id, proposal.target, proposal.change,
                        item_max_chars=item_max_chars)
