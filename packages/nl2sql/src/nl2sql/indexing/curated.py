"""Verified queries as index entries, and the hash that tells when they are stale.

Each phrasing of each verified query in ``curated.yaml`` becomes one
``curated.verified_query`` entry, and so does its description. A raw ``{n}``
embeds poorly, so placeholders are filled first: the param's ``example``, else
its ``default``, else its own name.

The entries are written in the datasource's build, beside its schema entries
(``IndexingOrchestrator``). ``curated_hash`` is recorded with the build as
``curated_hash:<datasource_id>``; index health compares it with the file as it
is now.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Dict, Iterable, List, Mapping, Optional

from nl2sql.indexing.models import VerifiedQueryChunk

if TYPE_CHECKING:
    from nl2sql.configs.curated import CuratedDatasource, CuratedFileConfig, ParamSpec

CURATED_HASH_KEY = "curated_hash:"

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fill_placeholders(question: str, params: Mapping[str, "ParamSpec"]) -> str:
    """``question`` with each ``{name}`` replaced by its example, default or name."""

    def value(match: re.Match) -> str:
        name = match.group(1)
        spec = params.get(name)
        if spec is None:
            return name
        for candidate in (spec.example, spec.default):
            if candidate is not None:
                return str(candidate)
        return name

    return _PLACEHOLDER.sub(value, question)


class CuratedChunkBuilder:
    """Builds a datasource's ``curated.verified_query`` entries."""

    def __init__(self, ds_id: str, curated: Optional["CuratedDatasource"], schema_version: str):
        self.ds_id = ds_id
        self.curated = curated
        self.schema_version = schema_version

    def build(self) -> List[VerifiedQueryChunk]:
        if self.curated is None:
            return []
        chunks: List[VerifiedQueryChunk] = []
        for query in self.curated.verified_queries:
            texts = [fill_placeholders(q, query.params) for q in query.questions] + [query.description]
            for n, text in enumerate(texts):
                chunks.append(VerifiedQueryChunk(
                    id=f"curated.verified_query:{self.ds_id}:{query.id}:{n}",
                    datasource_id=self.ds_id,
                    query_id=query.id,
                    text=text,
                    schema_version=self.schema_version,
                ))
        return chunks


def curated_hash(curated: Optional["CuratedDatasource"]) -> str:
    """SHA-256 of a datasource's verified queries, as canonical JSON.

    No section and an empty one hash alike. Metrics are left out: nothing
    indexes them yet, so editing one must not ask for a re-index.
    """
    queries = [q.model_dump(mode="json") for q in curated.verified_queries] if curated is not None else []
    canonical = json.dumps({"verified_queries": queries}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def curated_section(cfg: Optional["CuratedFileConfig"], ds_id: str) -> Optional["CuratedDatasource"]:
    return cfg.datasources.get(ds_id) if cfg is not None else None


def curated_hashes(cfg: Optional["CuratedFileConfig"], datasource_ids: Iterable[str]) -> Dict[str, str]:
    """The current hash of every datasource in ``datasource_ids``."""
    return {ds_id: curated_hash(curated_section(cfg, ds_id)) for ds_id in datasource_ids}
