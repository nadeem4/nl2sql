

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Dict, Any, List

from nl2sql_sdk.schema import TableRef

# Connection args are free-form extras, so secrets are recognised by name,
# by the same rule the config masks them with.
from nl2sql.config.models import SECRET_KEY_HINTS as SECRET_ARG_HINTS  # noqa: F401
from nl2sql.config.models import is_secret_key as _is_secret_arg

# Limits a datasource may set beside ``connection`` or under ``options``.
LIMIT_KEYS = ("row_limit", "statement_timeout_ms", "max_bytes")


class ConnectionConfig(BaseModel):
    """Database connection details.

    Once resolved, extras such as ``password`` hold plaintext, which the
    registry passes to the adapter via ``model_dump()``. ``repr``/``str`` mask
    them so a log line or traceback holding the config never shows them.
    """
    type: str

    model_config = {"extra": "allow"}

    def __repr_args__(self):
        for key, value in super().__repr_args__():
            if key and _is_secret_arg(key) and value:
                value = "**********"
            yield key, value

class DatasourceConfig(BaseModel):
    """Configuration for a single datasource."""
    id: str
    description: Optional[str] = None
    connection: ConnectionConfig
    options: Dict[str, Any] = Field(default_factory=dict)
    # Tables (usually views the data team built) the planner should reach for
    # first. The schema retriever ranks them first and marks them preferred.
    preferred_tables: List[str] = Field(default_factory=list)

    @field_validator("preferred_tables")
    @classmethod
    def _no_blank_preferred_tables(cls, value: List[str]) -> List[str]:
        names = [name.strip() for name in value]
        if any(not name for name in names):
            raise ValueError("preferred_tables holds a blank name")
        return names

    @model_validator(mode="before")
    @classmethod
    def _fold_top_level_limits(cls, data: Any) -> Any:
        """Move ``row_limit``, ``statement_timeout_ms`` and ``max_bytes`` into ``options``.

        A limit may be written beside ``connection`` or under ``options``; when
        both are set, the lower value wins.
        """
        if not isinstance(data, dict) or not any(k in data for k in LIMIT_KEYS):
            return data
        data = dict(data)
        options = dict(data.get("options") or {})
        for key in LIMIT_KEYS:
            top = data.pop(key, None)
            if top is None:
                continue
            nested = options.get(key)
            options[key] = top if nested is None else min(int(top), int(nested))
        data["options"] = options
        return data


def matches_table(name: str, ref: TableRef) -> bool:
    """Whether a ``preferred_tables`` entry names ``ref``.

    An entry is a bare table name, ``schema.table``, or the snapshot's
    ``[schema].[table]`` key; case is ignored.
    """
    name = name.strip().lower()
    table, schema = ref.table_name.lower(), (ref.schema_name or "").lower()
    return name in (table, f"{schema}.{table}", f"[{schema}].[{table}]")
