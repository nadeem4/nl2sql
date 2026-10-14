"""Which statistics a column type gets, and how a statistic becomes a scalar."""
from __future__ import annotations

import datetime
import decimal
from typing import Any, NamedTuple

from sqlalchemy.sql import sqltypes

# A text column with at most this many distinct values is indexed with all of
# them, so a filter on any one ('Heavy Metal' among 25 genres) matches a real
# value rather than a guess; a larger column keeps its five most frequent.
ALL_VALUES_MAX_DISTINCT = 50
SAMPLE_VALUES = 5

# Types MIN/MAX cannot order: PostgreSQL raises on MIN(boolean) and MIN(uuid),
# SQL Server on MIN(bit). Dialect types subclass these (postgresql UUID,
# mssql BIT and UNIQUEIDENTIFIER), so isinstance covers them.
_UNORDERED_TYPES = (sqltypes.Boolean, sqltypes.Uuid)
# Types that get no statistics at all, by class and, for types a driver
# reflects without a SQLAlchemy class, by name.
_UNPROFILED_TYPES = (sqltypes.JSON, sqltypes.LargeBinary, sqltypes.ARRAY)
_UNPROFILED_NAMES = ("json", "blob", "binary", "bytea", "xml", "array")
_TEXT_NAMES = ("char", "text", "string", "clob")


class ColumnProfile(NamedTuple):
    """The statistics a column gets under ``profile: stats``.

    Its null share is always read. ``values`` is only a permission: a column
    gets values only when the datasource's ``index.sample_values`` is on too.
    """

    min_max: bool
    distinct: bool
    values: bool


def column_profile(sa_type: Any) -> ColumnProfile:
    """The default rule: no MIN/MAX on boolean or uuid; only the null share of JSON, binary, XML and
    arrays; values only for text."""
    if _unprofiled(sa_type):
        return ColumnProfile(min_max=False, distinct=False, values=False)
    if isinstance(sa_type, _UNORDERED_TYPES):
        return ColumnProfile(min_max=False, distinct=True, values=False)
    is_text = any(word in type_name(sa_type).lower() for word in _TEXT_NAMES)
    return ColumnProfile(min_max=True, distinct=True, values=is_text)


def as_scalar(value: Any) -> Any:
    """``value`` as a statistics scalar (str, int, float, bool or None).

    Drivers return dates, times, decimals and UUIDs for MIN/MAX, which the
    statistics model does not accept: dates and times become ISO strings,
    decimals floats, anything else its text.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return str(value)


def type_name(sa_type: Any) -> str:
    try:
        return str(sa_type)
    except Exception:
        return type(sa_type).__name__


def _unprofiled(sa_type: Any) -> bool:
    if isinstance(sa_type, _UNPROFILED_TYPES):
        return True
    return any(name in type_name(sa_type).lower() for name in _UNPROFILED_NAMES)
