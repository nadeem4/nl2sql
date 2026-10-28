"""What writing a config needs: secrets written as references, the ``.env`` file, and a file replaced whole.

``write_project`` (``config/project.py``) and ``engine.datasources.save`` are the only callers. The rules are
docs/architecture/wave_c.md, section 7, "Writing the config" (D122, D123).

A secret is never written into YAML. A reference (``${env:VAR}``, ``${vault:path}``) is kept as it is; a literal
becomes ``${env:NL2SQL_<PATH>}`` with the value in the ``.env`` beside ``nl2sql.yaml``, where the writer may write
one, and is refused (``SecretValueInConfig``) where it may not. Nothing here reads or writes ``os.environ``.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import yaml
from pydantic import BaseModel, SecretStr

from nl2sql.common.errors import SecretValueInConfig
from nl2sql.config.models import Datasource, is_secret_key

_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_-]*:[^{}]+\}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_@%+=:,./-]*")


def env_name(path: str) -> str:
    """``NL2SQL_`` plus ``path`` in upper case, every character that is not a letter or digit as ``_``."""
    return "NL2SQL_" + re.sub(r"[^A-Za-z0-9]", "_", path).upper()


def file_sha256(path: Path) -> Optional[str]:
    """The SHA-256 of the file's bytes, or None when it does not exist."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


# ---------------------------------------------------------------- secrets

class EnvSecrets:
    """Turns each secret literal of one write into ``${env:NAME}`` and collects ``NAME -> value`` for the ``.env``.

    ``env_file`` is whether this writer may write a ``.env``; without it a literal is refused. ``reserved`` holds
    the names other files already use (``NAME -> (path, where)``), so a second field never takes one.
    """

    def __init__(self, *, env_file: bool, reserved: Optional[Dict[str, Tuple[str, str]]] = None):
        self.env_file = env_file
        self.values: Dict[str, str] = {}
        self._owners: Dict[str, Tuple[str, str]] = dict(reserved or {})

    def reference(self, where: str, path: str, value: Any) -> Any:
        """``value`` to write for the secret field ``where`` (named in a message), whose env path is ``path``."""
        if not isinstance(value, str) or not value or _REFERENCE.fullmatch(value):
            return value
        if not self.env_file:
            raise SecretValueInConfig(
                f"{where} holds a secret value, which would be written into YAML. "
                "Pass ${env:VAR} or a secret provider reference instead.")
        name = env_name(path)
        owner = self._owners.get(name)
        if owner is not None and owner[0] != path:
            raise ValueError(f"{where} and {owner[1]} both map to the environment variable {name}. "
                             "Rename one of the datasources or fields; a value is never overwritten.")
        self._owners[name] = (path, where)
        self.values[name] = value
        return f"${{env:{name}}}"


def reserved_names(datasources: Iterable[Datasource]) -> Dict[str, Tuple[str, str]]:
    """The ``NL2SQL_...`` names these datasources' connections already reference, with the field using each."""
    reserved: Dict[str, Tuple[str, str]] = {}
    for datasource in datasources:
        if not isinstance(datasource.connection, dict):
            continue
        for key, value in datasource.connection.items():
            match = isinstance(value, str) and re.fullmatch(r"\$\{env:(NL2SQL_[A-Za-z0-9_]*)\}", value)
            if match:
                reserved[match.group(1)] = (f"{datasource.id}.{key}", f"datasources/{datasource.id}.yaml connection.{key}")
    return reserved


def dump_model(model: BaseModel, what: str, **kwargs: Any) -> Any:
    """``model`` as plain data, as the files hold it. A value that is an object, not data, is a ``ValueError`` naming ``what``."""
    try:
        return model.model_dump(mode="json", by_alias=True, exclude_defaults=True, **kwargs)
    except (ValueError, TypeError):
        raise ValueError(f"{what} holds an object (an adapter, store, hook, observer or chat model), not data. "
                         "write_project writes data only: build the engine from code, or replace the object "
                         "with its name and settings.") from None


class _Dumper(yaml.SafeDumper):
    """Writes multi-line strings (the SQL) as ``|`` blocks, so a file reads as written."""


_Dumper.add_representer(
    str,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None),
)


def dump_yaml(data: Any) -> str:
    """``data`` as YAML in the order given, readable as written."""
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def check_file_name(datasource_id: str) -> str:
    """``datasource_id`` as a file name in ``datasources/``, or a ``ValueError``."""
    if not datasource_id or datasource_id in {".", ".."} or "/" in datasource_id or "\\" in datasource_id:
        raise ValueError(f"The datasource id '{datasource_id}' cannot be a file name in datasources/.")
    return f"{datasource_id}.yaml"


def datasource_data(datasource: Datasource, secrets: EnvSecrets) -> Dict[str, Any]:
    """One datasource as its file holds it (no ``id``: the file name is it), its connection's secrets as references."""
    where = f"datasources/{datasource.id}.yaml"
    connection = datasource.connection
    secret_keys = set()
    if isinstance(connection, BaseModel):
        plain = connection.model_dump(mode="python", exclude_defaults=True)
        secret_keys = {key for key, value in plain.items() if isinstance(value, SecretStr)}
        plain = {key: value.get_secret_value() if isinstance(value, SecretStr) else value
                 for key, value in plain.items()}
        datasource = datasource.model_copy(update={"connection": plain})
    data = dump_model(datasource, f"{where} (datasources: '{datasource.id}')", exclude={"id"})
    if isinstance(data.get("connection"), dict):
        data["connection"] = {
            key: secrets.reference(f"{where} connection.{key}", f"{datasource.id}.{key}", value)
            if (is_secret_key(key) or key in secret_keys) else value
            for key, value in data["connection"].items()}
    return data


def llm_data(data: Dict[str, Any], secrets: EnvSecrets) -> Dict[str, Any]:
    """The dumped ``llm`` section with the keys and header values of the LLM and of each agent as references."""
    def keys(block: Dict[str, Any], path: str) -> None:
        if "api_key" in block:
            block["api_key"] = secrets.reference(f"llm.yaml {path}api_key", f"llm.{path}api_key", block["api_key"])
        for name, value in (block.get("headers") or {}).items():
            block["headers"][name] = secrets.reference(f"llm.yaml {path}headers.{name}", f"llm.{path}headers.{name}", value)

    keys(data, "")
    for agent, block in (data.get("agents") or {}).items():
        keys(block, f"agents.{agent}.")
    return data


def embeddings_data(data: Dict[str, Any], secrets: EnvSecrets) -> Dict[str, Any]:
    if "api_key" in data:
        data["api_key"] = secrets.reference("embeddings.yaml api_key", "embeddings.api_key", data["api_key"])
    return data


def secrets_data(data: Dict[str, Any], secrets: EnvSecrets) -> Dict[str, Any]:
    """The dumped ``secrets`` section with each provider's secret settings as references."""
    for provider in data.get("providers") or []:
        for key in list(provider):
            if is_secret_key(key):
                provider[key] = secrets.reference(f"secrets.yaml provider '{provider.get('id')}' {key}",
                                                  f"secrets.{provider.get('id')}.{key}", provider[key])
    return data


# ---------------------------------------------------------------- files

def write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data``: a temporary file beside it, flushed to disk, renamed over it.

    A symlinked file is written through and the link kept; an existing file keeps its mode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    real = Path(os.path.realpath(path))
    handle, temporary = tempfile.mkstemp(prefix=f".{real.name}.", suffix=".tmp", dir=real.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if real.exists():
            shutil.copymode(real, temporary)
        os.replace(temporary, real)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def update_gitignore(path: Path, lines: Iterable[str]) -> bool:
    """Add the ``lines`` ``.gitignore`` is missing. Returns whether the file changed."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in lines if line not in text.splitlines()]
    if not missing:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "".join(f"{line}\n" for line in missing), encoding="utf-8")
    return True


def _value(text: str) -> str:
    if _PLAIN_VALUE.fullmatch(text):
        return text
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def write_env_values(folder: Any, values: Mapping[str, str]) -> Path:
    """Write ``values`` into the ``.env`` beside ``nl2sql.yaml`` in ``folder``, and return its path.

    A line with the same name is replaced; every other line is kept as it is; the file is created when needed and
    ``.gitignore`` is made to list it. It writes a file and never ``os.environ``: ``load_env_file`` is how an
    entry point reads it back, and it never overrides a variable that is already set.

    Raises:
        ValueError: a name is not an environment variable name, or a value spans lines. Nothing is written.
    """
    folder = Path(folder)
    for name, value in values.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"'{name}' is not an environment variable name.")
        if "\n" in value or "\r" in value:
            raise ValueError(f"The value of {name} spans lines; a .env line holds one.")
    path = folder / ".env"
    if not values:
        return path
    written: List[str] = []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        match = _ENV_LINE.match(line)
        if match and match.group(1) in values:
            if match.group(1) not in written:
                written.append(match.group(1))
                out.append(f"{match.group(1)}={_value(values[match.group(1)])}")
        else:
            out.append(line)
    out += [f"{name}={_value(value)}" for name, value in values.items() if name not in written]
    update_gitignore(folder / ".gitignore", [".env"])
    write_atomic(path, ("\n".join(out) + "\n").encode("utf-8"))
    return path
