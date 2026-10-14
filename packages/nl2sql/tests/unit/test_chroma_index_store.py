"""The ``chroma`` index store, on Chroma's public client API (docs/architecture/phase2.md, section 4).

The engine embeds and hands the store vectors; the store keeps entries in one
collection per namespace, filters with ``Filter`` and records each
datasource's live build as a pointer.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nl2sql.storage.chroma import ChromaIndex
from nl2sql_sdk.stores import BuildPointer, Entry, Filter

NS = "nl2sql__test-model__3"


def _entry(i, ds="shop", build="b1", kind="schema.table", vector=(1.0, 0.0, 0.0)):
    return Entry(id=f"{ds}:{build}:{i}", text=f"text {i}", vector=list(vector),
                 metadata={"datasource_id": ds, "build_id": build, "type": kind, "n": i})


def _pointer(build, version="v1"):
    return BuildPointer(build_id=build, schema_version=version, built_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.fixture
def store(tmp_path):
    index = ChromaIndex(tmp_path / "index")
    yield index
    index.close()


def test_building_the_store_writes_nothing(tmp_path):
    index = ChromaIndex.from_config(ChromaIndex.config_model(), folder=tmp_path)

    assert index.namespaces() == []
    assert index.pointers(NS) == {}
    assert index.get(NS, Filter()) == []
    assert not (tmp_path / "index").exists()


def test_add_then_get_and_query_by_filter(store):
    store.open(NS, 3)
    store.add(NS, [_entry(0), _entry(1, vector=(0.0, 1.0, 0.0)), _entry(2, ds="crm", kind="schema.column")])

    got = store.get(NS, Filter(eq={"datasource_id": "shop"}))
    assert sorted(e.id for e in got) == ["shop:b1:0", "shop:b1:1"]
    assert all(e.vector == [] for e in got)
    assert store.get(NS, Filter(eq={"datasource_id": "shop"}), with_vectors=True)[0].vector

    hits = store.query(NS, [1.0, 0.0, 0.0], k=5, where=Filter(**{"in": {"type": ["schema.table"]}}))
    assert [h.id for h in hits] == ["shop:b1:0", "shop:b1:1"]
    assert hits[0].distance <= hits[1].distance and hits[0].vector is None
    assert hits[0].metadata["n"] == 0 and hits[0].text == "text 0"
    assert store.query(NS, [1.0, 0.0, 0.0], k=5, where=Filter(ne={"datasource_id": "shop"}),
                       with_vectors=True)[0].vector == [1.0, 0.0, 0.0]


def test_delete_returns_how_many(store):
    store.open(NS, 3)
    store.add(NS, [_entry(0), _entry(1, build="b2"), _entry(2, build="b3")])

    assert store.delete(NS, Filter(eq={"datasource_id": "shop"}, **{"in": {"build_id": ["b1", "b3"]}})) == 2
    assert [e.id for e in store.get(NS, Filter())] == ["shop:b2:1"]


def test_an_empty_in_matches_nothing(store):
    store.open(NS, 3)
    store.add(NS, [_entry(0)])

    assert store.get(NS, Filter(**{"in": {"build_id": []}})) == []
    assert store.query(NS, [1.0, 0.0, 0.0], k=3, where=Filter(**{"in": {"build_id": []}})) == []
    assert store.delete(NS, Filter(**{"in": {"build_id": []}})) == 0


def test_set_pointers_merges_and_removes_in_one_step(store):
    store.open(NS, 3)
    store.set_pointers(NS, {"shop": _pointer("b1"), "crm": _pointer("c1")})
    store.set_pointers(NS, {"shop": _pointer("b2", "v2"), "crm": None})

    pointers = store.pointers(NS)
    assert set(pointers) == {"shop"}
    assert pointers["shop"].build_id == "b2" and pointers["shop"].schema_version == "v2"


def test_pointers_are_per_namespace(store):
    store.open(NS, 3)
    store.open("nl2sql__other__3", 3)
    store.set_pointers(NS, {"shop": _pointer("b1")})

    assert store.pointers("nl2sql__other__3") == {}
    assert sorted(store.namespaces()) == sorted([NS, "nl2sql__other__3"])


def test_open_refuses_another_dimension(store):
    store.open(NS, 3)

    with pytest.raises(ValueError, match="3"):
        store.open(NS, 4)


def test_drop_removes_the_namespace(store):
    store.open(NS, 3)
    store.add(NS, [_entry(0)])
    store.drop(NS)

    assert store.namespaces() == []
    assert store.get(NS, Filter()) == []
