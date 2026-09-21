

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

# Connection args are free-form extras, so secrets are recognised by name.
SECRET_ARG_HINTS = ("password", "secret", "token", "api_key")


def _is_secret_arg(key: str) -> bool:
    return any(hint in key.lower() for hint in SECRET_ARG_HINTS)


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



