"""Loading the knowledge sources and merging them in config order (docs/architecture/wave_b.md, section 3).

``load_knowledge(config, registry=, secrets=)`` builds each entry of the
``knowledge`` section (a ``KnowledgeSourceConfig`` through the registry, an
object as given), calls ``load()`` once, checks every text against
``settings.knowledge.item_max_chars``, and merges them into one ``Knowledge``:

- a single-valued field (a description, a role, a unit) is the first
  source's that sets it;
- lists (synonyms, terms, rules, joins, examples) are unioned, a duplicate
  (the same term name, rule id, join or text, case ignored) kept from the
  first source;
- each item records the source it came from (``provenance``);
- each datasource's merged knowledge has a digest: the SHA-256 of its
  canonical JSON, which keys the plan cache (D57) and the build pointer (D56).

``Knowledge`` is immutable: a reload builds a new one and the runtime swaps it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from nl2sql_sdk.knowledge import ColumnKnowledge, DatasourceKnowledge, Glossary, TableKnowledge

if TYPE_CHECKING:
    from nl2sql.config import Config
    from nl2sql.registry import Registry
    from nl2sql.secrets import SecretManager

KINDS = ("tables", "columns", "joins", "terms", "rules", "examples")
# Items already checked against the cap when their source loaded are not checked again as they move.
_UNCAPPED = {"item_max_chars": 1 << 30}


@dataclass(frozen=True)
class LoadedSource:
    """One source's glossary as it was loaded: its label (its ``type``), the hash and path of its file."""

    type: str
    glossary: Glossary
    sha256: Optional[str]
    path: Optional[Path]
    loaded_at: datetime


@dataclass(frozen=True)
class Knowledge:
    """The engine's knowledge: every source as loaded, the merged knowledge per datasource and its digest.

    ``errors`` maps a source to the last reload's error, when that reload
    failed and this knowledge stayed live.
    """

    sources: Tuple[LoadedSource, ...] = ()
    datasources: Dict[str, DatasourceKnowledge] = field(default_factory=dict)
    provenance: Dict[str, Dict[str, str]] = field(default_factory=dict)
    digests: Dict[str, str] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    def get(self, datasource_id: str) -> DatasourceKnowledge:
        return self.datasources.get(datasource_id) or DatasourceKnowledge()

    def digest(self, datasource_id: str) -> str:
        """The datasource's knowledge digest; a datasource with no knowledge has the digest of none."""
        return self.digests.get(datasource_id) or digest(DatasourceKnowledge())

    def failed(self, label: str, error: str) -> "Knowledge":
        """This knowledge, still live, with the reload error that left it in place."""
        return replace(self, errors={label: error})


class KnowledgeLoadError(ValueError):
    """A knowledge source could not be built or loaded. ``index`` is its place in the ``knowledge`` section."""

    def __init__(self, index: int, label: str, message: str):
        self.index, self.label = index, label
        super().__init__(message)


def digest(knowledge: DatasourceKnowledge) -> str:
    """SHA-256 of the canonical JSON of one datasource's knowledge."""
    data = knowledge.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def counts(knowledge: DatasourceKnowledge) -> Dict[str, int]:
    """How many items of each kind."""
    return {kind: len(getattr(knowledge, kind)) for kind in KINDS}


def source_label(entry: Any) -> str:
    """A source's name: its ``type``, or for an object its ``type`` attribute or class name."""
    label = getattr(entry, "type", None)
    return label if isinstance(label, str) and label else type(entry).__name__


def build_sources(config: "Config", *, registry: "Registry", secrets: "SecretManager") -> List[Tuple[str, Any]]:
    """Each entry of the ``knowledge`` section as ``(label, source)``, in config order.

    Raises:
        KnowledgeLoadError: a type that is not registered, or keys its
            ``config_model`` refuses.
    """
    from pydantic import ValidationError

    from nl2sql.config.models import KnowledgeSourceConfig
    from nl2sql.registry import RegistryError

    folder = config.folder if config.folder is not None else Path(".")
    built: List[Tuple[str, Any]] = []
    for i, entry in enumerate(config.knowledge):
        label = source_label(entry)
        if not isinstance(entry, KnowledgeSourceConfig):
            built.append((label, entry))
            continue
        try:
            cls = registry.load("knowledge_source", entry.type)
            cfg = cls.config_model.model_validate(secrets.resolve_object(dict(entry.model_extra or {})))
            source = cls.from_config(cfg, folder=folder)
        except RegistryError as e:
            raise KnowledgeLoadError(i, label, str(e)) from None
        except ValidationError as e:
            reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the block'}: {error['msg']}"
                                for error in e.errors())
            raise KnowledgeLoadError(i, label, f"The {entry.type} knowledge source's keys are not valid: "
                                               f"{reasons}.") from None
        except ValueError as e:
            raise KnowledgeLoadError(i, label, str(e)) from None
        built.append((label, source))
    return built


def load_knowledge(config: "Config", *, registry: "Registry", secrets: "SecretManager") -> Knowledge:
    """Build and load every source in ``config.knowledge`` and merge them. Never during a run.

    Raises:
        KnowledgeLoadError: the first source that cannot be built or loaded,
            or whose glossary breaks the item cap.
    """
    from nl2sql.knowledge.yaml_source import YamlKnowledgeSource, parse_glossary

    cap = config.settings.knowledge.item_max_chars
    loaded: List[LoadedSource] = []
    labels: Dict[str, int] = {}
    for i, (label, source) in enumerate(build_sources(config, registry=registry, secrets=secrets)):
        labels[label] = labels.get(label, 0) + 1
        if labels[label] > 1:
            label = f"{label}#{labels[label]}"
        if isinstance(source, YamlKnowledgeSource):
            source.item_max_chars = cap
        try:
            glossary = source.load()
            if not isinstance(glossary, Glossary):
                raise ValueError(f"load() returned {type(glossary).__name__}, not a Glossary")
            # Every source is held to the engine's cap, a plugin's too.
            glossary = parse_glossary(glossary.model_dump(mode="json", by_alias=True, exclude_unset=True),
                                      item_max_chars=cap, source=f"The {label} knowledge source")
        except Exception as e:  # a source is anyone's code: whatever it raises is its load error
            raise KnowledgeLoadError(i, label, str(e) or type(e).__name__) from None
        path = getattr(source, "path", None)
        loaded.append(LoadedSource(type=label, glossary=glossary, sha256=getattr(source, "sha256", None),
                                   path=Path(path) if path is not None else None,
                                   loaded_at=datetime.now(timezone.utc)))
    return merge(loaded)


def loaded_event(knowledge: Knowledge, error: Optional[str] = None) -> Dict[str, Any]:
    """The data of ``knowledge.loaded``: each source's counts and hash, the digests, and a reload's error."""
    return {"sources": [{"type": s.type, "sha256": s.sha256,
                         "counts": {ds: counts(k) for ds, k in s.glossary.datasources.items()}}
                        for s in knowledge.sources],
            "digests": dict(knowledge.digests), "error": error}


# ---------------------------------------------------------------- merging


def _key(name: str) -> str:
    return name.strip().lower()


def _join_key(join) -> str:
    def side(value):
        return ",".join(_key(v) for v in ([value] if isinstance(value, str) else value))

    return f"{side(join.from_)}->{side(join.to)}"


def _join_label(join) -> str:
    def side(value):
        return value if isinstance(value, str) else ",".join(value)

    return f"{side(join.from_)}->{side(join.to)}"


def _merge_fields(first: Optional[Any], later: Any, cls, path: str, label: str,
                  provenance: Dict[str, str]):
    """A table's or column's knowledge: each field from the first source that sets it; lists unioned."""
    data = first.model_dump(by_alias=True, exclude_unset=True) if first is not None else {}
    for name, value in later.model_dump(by_alias=True, exclude_unset=True).items():
        if name == "synonyms":
            seen = {_key(s) for s in data.get("synonyms", [])}
            added = [s for s in value if _key(s) not in seen]
            if added:
                data["synonyms"] = [*data.get("synonyms", []), *added]
                provenance.setdefault(f"{path}.synonyms", label)
        elif name == "values":
            merged = dict(value)
            merged.update(data.get("values", {}))
            if merged:
                data["values"] = merged
                provenance.setdefault(f"{path}.values", label)
        elif data.get(name) is None and value is not None:
            data[name] = value
            provenance[f"{path}.{name}"] = label
    return cls.model_validate(data, context=_UNCAPPED)


def merge(sources: Sequence[LoadedSource]) -> Knowledge:
    """The sources' glossaries merged in order: the first wins a single value, lists are unioned."""
    merged: Dict[str, Dict[str, Any]] = {}
    provenance: Dict[str, Dict[str, str]] = {}
    for source in sources:
        label = source.type
        for ds_id, knowledge in source.glossary.datasources.items():
            block = merged.setdefault(ds_id, {"tables": {}, "columns": {}, "joins": {}, "terms": {}, "rules": {},
                                              "examples": {}})
            where = provenance.setdefault(ds_id, {})
            for kind, cls in (("tables", TableKnowledge), ("columns", ColumnKnowledge)):
                for name, item in getattr(knowledge, kind).items():
                    existing = next((n for n in block[kind] if _key(n) == _key(name)), None)
                    key = existing or name
                    block[kind][key] = _merge_fields(block[kind].get(key), item, cls, f"{kind}.{key}", label, where)
            for join in knowledge.joins:
                if _join_key(join) not in block["joins"]:
                    block["joins"][_join_key(join)] = join
                    where[f"joins.{_join_label(join)}"] = label
            for term in knowledge.terms:
                if _key(term.term) not in block["terms"]:
                    block["terms"][_key(term.term)] = term
                    where[f"terms.{term.term}"] = label
            for rule in knowledge.rules:
                if rule.id not in block["rules"]:
                    block["rules"][rule.id] = rule
                    where[f"rules.{rule.id}"] = label
            for example in knowledge.examples:
                if _key(example) not in block["examples"]:
                    block["examples"][_key(example)] = example
                    where[f"examples.{example}"] = label
    # Every item was checked against the cap as its source loaded; the merge only moves them.
    datasources = {
        ds_id: DatasourceKnowledge.model_validate(
            {"tables": block["tables"], "columns": block["columns"], "joins": list(block["joins"].values()),
             "terms": list(block["terms"].values()), "rules": list(block["rules"].values()),
             "examples": list(block["examples"].values())}, context=_UNCAPPED)
        for ds_id, block in merged.items()
    }
    return Knowledge(sources=tuple(sources), datasources=datasources, provenance=provenance,
                     digests={ds_id: digest(k) for ds_id, k in datasources.items()})
