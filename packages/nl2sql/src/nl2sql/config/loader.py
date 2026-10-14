"""``load_config``: ``nl2sql.yaml`` and its included files, into one ``Config``.

The rules are section 2 of docs/architecture/configuration_v2.md:

- a section is inline, or exactly ``{file: <path>}``, relative to
  ``nl2sql.yaml``; only ``nl2sql.yaml`` includes, and only it has ``version``;
- ``datasources`` may name a folder: one datasource per ``*.yaml``, and its
  ``id`` is the file name;
- a ``${provider:key}`` reference is a whole value; secret fields and a
  datasource's ``connection`` keep it, everything else resolves ``${env:...}``
  now;
- every problem is collected and raised as one ``ConfigError``.

``load_env_file`` is for the entry points only: the library never reads a
``.env`` file.
"""
from __future__ import annotations

import difflib
import os
import re
import types
import typing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from typing_extensions import Annotated

from nl2sql.config.errors import MAPPING_PAGE, ConfigError, Problem
from nl2sql.config.models import KEEPS_REFERENCES, Config, Storage

PathLike = Union[str, "os.PathLike[str]"]
Loc = Tuple[Union[str, int], ...]

_UNIONS = (Union, getattr(types, "UnionType", Union))

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_-]*):([^{}]+)\}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def config_path(path: Optional[PathLike] = None) -> Path:
    """``path``, else ``$NL2SQL_CONFIG``, else ``./nl2sql.yaml``."""
    if path is None:
        path = os.environ.get("NL2SQL_CONFIG") or "nl2sql.yaml"
    return Path(path)


def load_config(path: Optional[PathLike] = None) -> Config:
    """Build the ``Config`` that ``nl2sql.yaml`` (and the files it includes) describe.

    ``path`` defaults to ``$NL2SQL_CONFIG``, then ``./nl2sql.yaml``. Raises one
    ``ConfigError`` that lists every problem found.
    """
    return _Loader(config_path(path)).load()


def load_env_file(path: Optional[PathLike] = None) -> Optional[Path]:
    """Load the ``.env`` next to ``nl2sql.yaml`` into the process environment.

    For the entry points (the CLI, ``nl2sql-api``, the demo) only. A variable
    that is already set is never overridden. Returns the file loaded, or
    ``None`` when there is none.
    """
    env_file = config_path(path).absolute().parent / ".env"
    if not env_file.is_file():
        return None
    from dotenv import load_dotenv

    load_dotenv(env_file, override=False)
    return env_file


# ---------------------------------------------------------------- annotations

def _members(annotation: Any) -> List[Any]:
    """``annotation`` with ``Annotated`` stripped and a union split into its members."""
    if typing.get_origin(annotation) is Annotated:
        return _members(typing.get_args(annotation)[0])
    if typing.get_origin(annotation) in _UNIONS:
        return [m for arg in typing.get_args(annotation) for m in _members(arg)]
    return [annotation]


def _keeps_references(annotation: Any) -> bool:
    if typing.get_origin(annotation) is Annotated:
        args = typing.get_args(annotation)
        return KEEPS_REFERENCES in args[1:] or _keeps_references(args[0])
    if typing.get_origin(annotation) in _UNIONS:
        return any(_keeps_references(a) for a in typing.get_args(annotation))
    return False


def _model(annotation: Any) -> Optional[type]:
    for member in _members(annotation):
        if isinstance(member, type) and issubclass(member, BaseModel):
            return member
    return None


def _item(annotation: Any, container: type) -> Any:
    """The item type of a ``list`` annotation, or the value type of a ``dict`` one."""
    for member in _members(annotation):
        if typing.get_origin(member) is container:
            return typing.get_args(member)[-1] if typing.get_args(member) else Any
    return None


def _field(model: type, key: Any) -> Optional[FieldInfo]:
    for name, field in model.model_fields.items():
        if key == name or key == field.alias:
            return field
    return None


def _keys(model: type) -> List[str]:
    return [field.alias or name for name, field in model.model_fields.items()]


def _annotation_at(loc: Loc) -> Any:
    annotation: Any = Config
    for part in loc:
        model = _model(annotation)
        if model is not None and isinstance(part, str):
            field = _field(model, part)
            if field is None:
                return None
            annotation = field.annotation
        else:
            annotation = _item(annotation, list if isinstance(part, int) else dict)
        if annotation is None:
            return None
    return annotation


def _format(loc: Loc) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _sentence(text: str) -> str:
    text = text[0].upper() + text[1:] if text else text
    return text if text.endswith((".", "?", "!")) else text + "."


# ---------------------------------------------------------------- the loader

class _Loader:
    def __init__(self, path: Path):
        self.shown = path.as_posix()
        self.path = path.absolute()
        self.root = self.path.parent
        self.name = self.path.name
        self.problems: List[Problem] = []
        self.failed: List[Loc] = []  # problems already reported here; validation stays quiet
        # Where each section came from: ("inline", None), ("file", label), ("folder", [labels]).
        self.sources: Dict[str, Tuple[str, Any]] = {}
        self.included: Dict[Path, str] = {}

    # -- reporting

    def _problem(self, loc: Loc, message: str, fix: Optional[str] = None) -> None:
        file, path = self._locate(loc)
        self.problems.append(Problem(file=file, path=path, message=message, fix=fix))
        self.failed.append(loc)

    def _file_problem(self, file: str, path: str, message: str, loc: Loc) -> None:
        self.problems.append(Problem(file=file, path=path, message=message))
        self.failed.append(loc)

    def _locate(self, loc: Loc) -> Tuple[str, str]:
        if not loc or not isinstance(loc[0], str) or loc[0] not in self.sources:
            return self.name, _format(loc)
        kind, label = self.sources[loc[0]]
        if kind == "file":
            return label, _format(loc[1:])
        if kind == "folder" and len(loc) > 1 and isinstance(loc[1], int) and loc[1] < len(label):
            return label[loc[1]], _format(loc[2:])
        return self.name, _format(loc)

    def _fail(self) -> ConfigError:
        return ConfigError(self.problems, source=self.name)

    # -- reading

    def _read(self, path: Path, label: str, loc: Loc) -> Tuple[bool, Any]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._file_problem(label, "", f"Cannot be read: {exc.strerror}.", loc)
            return False, None
        try:
            return True, yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
            problem = getattr(exc, "problem", None) or "it does not parse"
            self._file_problem(label, "", f"Not valid YAML{where}: {problem}.", loc)
            return False, None

    def load(self) -> Config:
        if not self.path.is_file():
            raise ConfigError([Problem(
                file=self.name, path="", message=f"{self.shown} not found.",
                fix="Write nl2sql.yaml (`nl2sql init`), or point NL2SQL_CONFIG at it.")], source=self.name)
        ok, data = self._read(self.path, self.name, ())
        if not ok:
            raise self._fail()
        if not isinstance(data, dict):
            raise ConfigError([Problem(self.name, "", "Must be a mapping of sections.")], source=self.name)
        self._check_version(data)

        data = dict(data)
        for section in list(data):
            if section in Config.model_fields and section != "version":
                self._include(data, section)
                if section not in data:
                    self.failed.append((section,))  # reported; not "missing" as well
        data = self._walk(data, Config, (), keep=False)

        config = self._validate(data)
        if self.problems:
            raise self._fail()
        config = self._anchor_storage(config)
        config._folder = self.root
        return config

    def _check_version(self, data: Dict[str, Any]) -> None:
        version = data.get("version")
        if version == 2:
            return
        if "version" not in data:
            message = "There is no `version`. A 0.5 nl2sql.yaml starts with `version: 2`."
        elif version == 1:
            message = (f"`{self.shown}` is a 0.4 config file (`version: 1`). "
                       f"0.5 reads `nl2sql.yaml`: see {MAPPING_PAGE}.")
        else:
            message = f"version {version!r} is not supported. A 0.5 nl2sql.yaml has `version: 2`."
        raise ConfigError([Problem(
            file=self.name, path="version", message=message,
            fix=f"Map each 0.4 file and variable with the table in {MAPPING_PAGE}.")], source=self.name)

    # -- file: includes

    def _include(self, data: Dict[str, Any], section: str) -> None:
        value = data[section]
        self.sources[section] = ("inline", None)
        if not (isinstance(value, dict) and "file" in value):
            return
        loc: Loc = (section,)
        if set(value) != {"file"}:
            del data[section]
            return self._problem(loc, "A section is either inline or exactly {file: <path>}, with no other keys.")
        written = value["file"]
        if not isinstance(written, str) or not written.strip():
            del data[section]
            return self._problem(loc + ("file",), "file: must be a path.")
        if "${" in written:
            del data[section]
            return self._problem(loc + ("file",), "file: values are literal; a ${...} reference is not resolved there.")

        target = self.root / written
        label = Path(written).as_posix()
        if target.is_dir() or written.endswith(("/", "\\")):
            if section != "datasources":
                del data[section]
                return self._problem(loc + ("file",), f"{label} is a folder; only datasources may include a folder.")
            if not target.is_dir():
                del data[section]
                return self._problem(loc + ("file",), f"The folder {label} was not found.")
            return self._include_folder(data, section, target, label)
        if not target.is_file():
            del data[section]
            return self._problem(loc + ("file",), f"{label} not found.")
        if not self._once(target, label, loc + ("file",)):
            del data[section]
            return

        self.sources[section] = ("file", label)
        ok, body = self._read(target, label, loc)
        body = self._body(body, label, loc, as_list=section == "datasources") if ok else None
        if body is None:
            del data[section]
        else:
            data[section] = body

    def _include_folder(self, data: Dict[str, Any], section: str, folder: Path, label: str) -> None:
        loc: Loc = (section,)
        datasources: List[Any] = []
        labels: List[str] = []
        for path in sorted(p for p in folder.glob("*.yaml") if p.is_file()):
            file_label = f"{label}/{path.name}"
            if not self._once(path, file_label, loc + ("file",)):
                continue
            ok, body = self._read(path, file_label, loc)
            body = self._body(body, file_label, loc, as_list=False) if ok else None
            if body is None:
                continue
            if "id" not in body:
                body = {"id": path.stem, **body}
            elif body["id"] != path.stem:
                self._file_problem(file_label, "id",
                                   f"id '{body['id']}' must equal the file name, '{path.stem}'.", loc)
                continue
            datasources.append(body)
            labels.append(file_label)
        self.sources[section] = ("folder", labels)
        data[section] = datasources

    def _once(self, path: Path, label: str, loc: Loc) -> bool:
        key = path.resolve()
        if key in self.included:
            self._problem(loc, f"{label} is included more than once.")
            return False
        self.included[key] = label
        return True

    def _body(self, body: Any, label: str, loc: Loc, as_list: bool) -> Any:
        """The section body an included file holds, or ``None`` after a problem."""
        if body is None:
            return [] if as_list else {}
        if isinstance(body, dict):
            if set(body) == {"file"}:
                self._file_problem(label, "file", "An included file holds the section's body; it cannot include "
                                   "another file. Only nl2sql.yaml includes.", loc)
                return None
            if "version" in body:
                self._file_problem(label, "version", "Only nl2sql.yaml has a version.", loc + ("version",))
                body = {k: v for k, v in body.items() if k != "version"}
        if as_list and not isinstance(body, list):
            self._file_problem(label, "", "Must hold the list of datasources.", loc)
            return None
        if not as_list and not isinstance(body, dict):
            self._file_problem(label, "", "Must hold a mapping of keys.", loc)
            return None
        return body

    # -- ${provider:key}

    def _walk(self, value: Any, annotation: Any, loc: Loc, keep: bool) -> Any:
        if isinstance(value, str):
            return self._interpolate(value, loc, keep)
        if isinstance(value, dict):
            model = _model(annotation)
            if model is None:
                item = _item(annotation, dict)
                return {k: self._walk(v, item, loc + (k,), keep) for k, v in value.items()}
            out = {}
            for key, child in value.items():
                field = _field(model, key)
                if field is not None:
                    child_keep = keep or KEEPS_REFERENCES in field.metadata or _keeps_references(field.annotation)
                    out[key] = self._walk(child, field.annotation, loc + (key,), child_keep)
                elif model.model_config.get("extra") == "allow":
                    # A block the component validates itself (a secret provider's settings).
                    out[key] = self._walk(child, None, loc + (key,), True)
                else:
                    out[key] = child  # an unknown key: validation reports it
            return out
        if isinstance(value, list):
            item = _item(annotation, list)
            return [self._walk(v, item, loc + (i,), keep) for i, v in enumerate(value)]
        return value

    def _interpolate(self, value: str, loc: Loc, keep: bool) -> str:
        match = _REFERENCE.fullmatch(value)
        if match is None:
            if "${" in value:
                self._problem(loc, "A ${provider:key} reference must be the whole value, not part of a string.",
                              fix="Put the whole value in the secret or variable it comes from.")
            return value
        provider, key = match.groups()
        if provider == "env" and not _ENV_NAME.fullmatch(key):
            self._problem(loc, f"'{key}' is not an environment variable name; there is no default syntax.")
            return value
        if loc[:1] == ("secrets",) and provider != "env":
            self._problem(loc, "Inside secrets, only ${env:...} references are allowed: "
                               "one secret provider cannot depend on another.")
            return value
        if keep:
            return value
        if provider != "env":
            self._problem(loc, f"Only ${{env:...}} resolves here. A ${{{provider}:...}} reference is allowed "
                               "only in a secret field or under a datasource's connection.")
            return value
        resolved = os.environ.get(key)
        if resolved is None:
            self._problem(loc, f"The environment variable {key} is not set.",
                          fix=f"Set {key}, or write the value instead of the reference.")
            return value
        return resolved

    # -- validation

    def _validate(self, data: Dict[str, Any]) -> Optional[Config]:
        try:
            return Config.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors(include_url=False, include_input=False):
                self._validation_problem(tuple(error["loc"]), error)
            return None

    def _validation_problem(self, loc: Loc, error: Dict[str, Any]) -> None:
        if any(loc[:len(failed)] == failed for failed in self.failed):
            return
        kind = error["type"]
        if kind == "extra_forbidden":
            model = _model(_annotation_at(loc[:-1]))
            return self._unknown(loc, _keys(model) if model else [])
        if loc and loc[-1] == "[key]" and kind == "literal_error":
            expected = re.findall(r"'([^']+)'", str(error.get("ctx", {}).get("expected", "")))
            return self._unknown(loc[:-1], expected)
        if kind == "missing":
            message = (f"The {loc[0]} section is required." if len(loc) == 1
                       else "This key is required.")
            return self._problem(loc, message)
        message = error["msg"]
        if message.startswith(("Value error, ", "Assertion failed, ")):
            message = message.split(", ", 1)[1]
        self._problem(loc, _sentence(message))

    def _unknown(self, loc: Loc, known: List[str]) -> None:
        close = difflib.get_close_matches(str(loc[-1]), known, n=1, cutoff=0.6)
        self._problem(loc, f"Unknown key. Did you mean '{close[0]}'?" if close else "Unknown key.")

    def _anchor_storage(self, config: Config) -> Config:
        """A relative ``storage.path`` in a file is relative to ``nl2sql.yaml``."""
        if config.storage.path.is_absolute():
            return config
        return config.model_copy(update={"storage": Storage(path=self.root / config.storage.path)})
