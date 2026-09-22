"""Does the written answer say what the result rows say? A deterministic check, no model.

Tier 1 and tier 2 score the rows a question returns. The answer synthesizer
then writes a sentence about them, and that sentence can state a number the
rows do not hold while the rows are right. :func:`check_answer` reads the
numbers and the quoted or bold names out of the answer and checks each one:

* a number is supported when it equals a value in the rows at the precision
  it is written with (``523.1`` matches ``523.06``, ``523.60`` does not), the
  row count, or the sum of a numeric column; a percentage may also be a share
  (``37.2%`` matches ``0.3718``). Numbers inside text cells count as values
  (the year of ``2009-01-01``), and so do numbers in the question (``top 5``,
  ``in 2013``);
* ordinals (``2nd``), list markers (``1.``), identifiers (``chinook_001``,
  ``MPEG-4``, ``#7``) and the month and day of a date are not read as numbers;
* a name is checked only when the answer quotes it or puts it in bold, and is
  supported when a text cell, a column name or the question contains it (or
  it contains a text cell). Bold labels (``**Note:**``) are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

# A number not glued to a word, an identifier or another number: ``1,234.50``,
# ``$5``, ``12.5%``, ``-5``. ``2nd`` and ``chinook_001`` do not match.
_NUMBER = re.compile(
    r"(?<![\w.,#\-])(?P<num>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<pct>\s?%|\s?percent\b)?(?!\w|\.\d)"
)
_LIST_MARKER = re.compile(r"^(\s*)\d+[.)](?=\s)", re.MULTILINE)
_QUOTED = re.compile(r"\"([^\"\n]{2,60})\"|“([^”\n]{2,60})”|\*\*([^*\n]{2,60})\*\*")
_MAX_ENTITY_WORDS = 6


@dataclass(frozen=True)
class WrittenNumber:
    text: str       # as written, without a currency sign or percent
    value: float
    decimals: int
    percent: bool


def extract_numbers(text: str) -> List[WrittenNumber]:
    """The numbers written in ``text``, in order, list markers and ordinals left out."""
    text = _LIST_MARKER.sub(r"\1", text or "")
    found = []
    for m in _NUMBER.finditer(text):
        raw = m.group("num")
        plain = raw.replace(",", "")
        decimals = len(plain.split(".")[1]) if "." in plain else 0
        found.append(WrittenNumber(raw, float(plain), decimals, bool(m.group("pct"))))
    return found


def _numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _close(written: float, value: float, decimals: int) -> bool:
    return abs(written - value) <= 0.5 * 10 ** -decimals + 1e-9


def _supported(n: WrittenNumber, values: Sequence[float]) -> bool:
    if any(_close(n.value, v, n.decimals) for v in values):
        return True
    # 37.2% of a share 0.3718: compare the share as a percent.
    return n.percent and any(_close(n.value, v * 100, n.decimals) for v in values)


def _entities(text: str) -> List[str]:
    """Quoted and bold spans that look like a value: no digits, not a label, a few words."""
    out = []
    for m in _QUOTED.finditer(text or ""):
        span = next(g for g in m.groups() if g is not None).strip()
        if (not span or span.endswith(":") or any(c.isdigit() for c in span)
                or len(span.split()) > _MAX_ENTITY_WORDS):
            continue
        out.append(span)
    return out


def check_answer(text: Optional[str], *, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                 question: str = "", row_counts: Sequence[int] = ()) -> Dict[str, Any]:
    """Checks the answer text against the rows it was written from.

    Args:
        text: The answer synthesizer's text (summary and content).
        columns: Column names of the result rows (every result set's).
        rows: The result rows.
        question: The question asked; numbers and names it contains are supported.
        row_counts: Each result set's total row count, when there are several
            or the rows are a sample; ``len(rows)`` is always supported.

    Returns:
        ``faithful`` (no unsupported number or name), ``unsupported_numbers``
        and ``unsupported_entities`` as written, and ``checked``: how many
        numbers and names were checked.
    """
    cells = [c for row in rows for c in row]
    texts = [str(c) for c in cells if isinstance(c, str)]
    values = [float(c) for c in cells if _numeric(c)]
    values += [n.value for t in texts for n in extract_numbers(t)]
    values += [n.value for n in extract_numbers(question)]
    values += [float(len(rows)), *(float(n) for n in row_counts)]
    for i in range(max((len(r) for r in rows), default=0)):
        column = [r[i] for r in rows if i < len(r) and _numeric(r[i])]
        if column:
            values.append(float(sum(column)))

    numbers = extract_numbers(text or "")
    unsupported_numbers = [n.text for n in numbers if not _supported(n, values)]

    known = [t.casefold() for t in texts] + [str(c).casefold() for c in columns]
    q = (question or "").casefold()
    entities = _entities(text or "")
    unsupported_entities = [
        e for e in entities
        if not (e.casefold() in q or any(e.casefold() in k or (len(k) >= 3 and k in e.casefold()) for k in known))
    ]
    return {
        "faithful": not unsupported_numbers and not unsupported_entities,
        "unsupported_numbers": unsupported_numbers,
        "unsupported_entities": unsupported_entities,
        "checked": len(numbers) + len(entities),
    }


def answer_text(final_answer: Optional[Dict[str, Any]]) -> str:
    """The text the answer synthesizer wrote: its summary, then its content."""
    if not final_answer:
        return ""
    parts = [final_answer.get("summary"), final_answer.get("content")]
    return "\n".join(p for p in parts if isinstance(p, str) and p)
