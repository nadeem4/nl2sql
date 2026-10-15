"""Knowledge sources, merging, resolution, the scope filter, show, export and reload (K1).

docs/architecture/wave_b.md, section 3, and decisions D36 to D39, D60 and D61.
The engine tests build a real engine on a small SQLite database with a fake
embedder, so nothing is downloaded and no model is called.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, ConfigError, Embeddings, Registry, UserContext
from nl2sql.knowledge.apply import apply_change
from nl2sql.knowledge.merge import LoadedSource, merge
from nl2sql.knowledge.resolve import derived_roles, problems
from nl2sql.knowledge.scope import denied_table_names, visible
from nl2sql.knowledge.yaml_source import (
    YamlKnowledgeSource,
    YamlSourceConfig,
    canonical_yaml,
    file_sha256,
    write_glossary,
)
from nl2sql.review.models import ReviewError, ReviewErrorCode
from nl2sql.testing.config import make_config
from nl2sql_sdk.knowledge import DatasourceKnowledge, Glossary, KnowledgeSource
from nl2sql_sdk.schema import (
    ColumnContract,
    ForeignKeyContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

GLOSSARY = {
    "version": 1,
    "datasources": {
        "shop": {
            "tables": {
                "orders": {"description": "One row per order.", "notes": "Kept for seven years."},
                "salaries": {"description": "Monthly pay."},
                "customers": {"description": "People who buy. Their pay is never in Salaries."},
            },
            "columns": {
                "orders.total": {"description": "Order total in USD.", "role": "money", "unit": "USD"},
                "orders.id": {"role": "quantity"},
                "salaries.amount": {"description": "Pay"},
            },
            "joins": [
                {"from": "orders.customer_id", "to": "customers.id", "cardinality": "many-to-one"},
                {"from": "salaries.id", "to": "customers.id"},
            ],
            "terms": [
                {"term": "revenue", "synonyms": ["sales"], "definition": "Sum of orders.total.",
                 "columns": ["orders.total"]},
                {"term": "payroll", "definition": "Sum of pay.", "tables": ["salaries"]},
                {"term": "cost", "definition": "Everything in salaries plus stock."},
            ],
            "rules": [
                {"id": "no_test", "text": "Leave out test customers.", "tables": ["customers"]},
                {"id": "pay_private", "text": "Never show SALARIES per person."},
            ],
            "examples": ["What is revenue by month?", "Who earns the most in salaries?"],
        }
    },
}

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "All but pay", "allow": ["shop"], "deny": ["shop.main.salaries"]}}
ADMIN = UserContext(roles=["admin"])
ANALYST = UserContext(roles=["analyst"])
NOBODY = UserContext(roles=["guest"])


def _write_glossary(folder: Path, data=GLOSSARY, name: str = "glossary.yaml") -> Path:
    path = folder / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _engine(tmp_path: Path, knowledge=None, **sections) -> NL2SQL:
    if not (tmp_path / "shop.db").exists():
        with sqlite3.connect(tmp_path / "shop.db") as connection:
            for statement in (
                "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, created_at DATE)",
                "CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount REAL)",
            ):
                connection.execute(statement)
        connection.close()
    if not (tmp_path / "glossary.yaml").exists():
        _write_glossary(tmp_path)
    knowledge = [{"type": "yaml", "path": "glossary.yaml"}] if knowledge is None else knowledge
    cfg = make_config(tmp_path, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                      policies={"roles": ROLES}, knowledge=knowledge, **sections)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                    dimension=8)}))


@pytest.fixture
def engine(tmp_path):
    with _engine(tmp_path) as engine:
        assert engine.index.build().ok
        yield engine


def _snapshot(tables: dict, schema: str = "main", foreign_keys: dict = None) -> SchemaSnapshot:
    """A snapshot by hand: ``{"orders": {"id": "INTEGER pk", "total": "REAL"}}``; a key ``a.b`` is in schema ``a``."""
    contracts, metadata = {}, {}
    for key, columns in tables.items():
        table_schema, _, name = key.rpartition(".")
        ref = TableRef(schema_name=table_schema or schema, table_name=name)
        contracts[key] = TableContract(table=ref, columns={
            column: ColumnContract(name=column, data_type=spec.split()[0], is_primary_key=spec.endswith(" pk"))
            for column, spec in columns.items()}, foreign_keys=(foreign_keys or {}).get(key, []))
        metadata[key] = TableMetadata(table=ref)
    return SchemaSnapshot(contract=SchemaContract(datasource_id="shop", engine_type="sqlite", tables=contracts),
                          metadata=SchemaMetadata(datasource_id="shop", engine_type="sqlite", tables=metadata))


SHOP_SNAPSHOT = _snapshot({
    "customers": {"id": "INTEGER pk", "name": "TEXT", "email": "TEXT"},
    "orders": {"id": "INTEGER pk", "customer_id": "INTEGER", "total": "REAL", "created_at": "DATE"},
    "salaries": {"id": "INTEGER pk", "amount": "REAL"},
})


# ---------------------------------------------------------------- the yaml source


def test_yaml_source_loads_a_glossary(tmp_path):
    path = _write_glossary(tmp_path)
    source = YamlKnowledgeSource.from_config(YamlSourceConfig(path="glossary.yaml"), folder=tmp_path)

    assert isinstance(source, KnowledgeSource)
    assert YamlKnowledgeSource.interface_version == 1
    glossary = source.load()
    assert glossary.datasources["shop"].columns["orders.total"].role == "money"
    assert source.sha256 == file_sha256(path)


def test_yaml_source_is_the_registered_entry_point():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert '[project.entry-points."nl2sql.knowledge_sources"]' in text
    assert 'yaml = "nl2sql.knowledge.yaml_source:YamlKnowledgeSource"' in text


def test_missing_glossary_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError) as caught:
        _engine(tmp_path, knowledge=[{"type": "yaml", "path": "glosary.yaml"}])
    message = str(caught.value)
    assert "knowledge" in message and "glosary.yaml" in message and "nl2sql init" in message


def test_an_invalid_glossary_is_a_config_error_naming_the_item(tmp_path):
    bad = {"version": 1, "datasources": {"shop": {"tables": {"orders": {"description": "x" * 501}}}}}
    _write_glossary(tmp_path, bad)
    with pytest.raises(ConfigError) as caught:
        _engine(tmp_path)
    assert "orders" in str(caught.value) and "500" in str(caught.value)


def test_the_item_cap_follows_the_setting(tmp_path):
    long = {"version": 1, "datasources": {"shop": {"tables": {"orders": {"description": "x" * 600}}}}}
    _write_glossary(tmp_path, long)
    with _engine(tmp_path, settings={"knowledge": {"item_max_chars": 700}}) as engine:
        assert engine.knowledge.sources(user_context=ADMIN)[0].error is None
    with pytest.raises(ConfigError):
        _engine(tmp_path, settings={"knowledge": {"item_max_chars": 100}})


def test_the_yaml_source_reads_yaml_safely(tmp_path):
    (tmp_path / "glossary.yaml").write_text("!!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    source = YamlKnowledgeSource(tmp_path / "glossary.yaml")
    with pytest.raises(ValueError):
        source.load()


# ---------------------------------------------------------------- the writer


def test_write_is_canonical_and_round_trips(tmp_path):
    glossary = Glossary.model_validate(GLOSSARY)
    text = canonical_yaml(glossary)

    assert text.startswith("# ")
    assert Glossary.model_validate(yaml.safe_load(text)) == glossary
    assert canonical_yaml(Glossary.model_validate(yaml.safe_load(text))) == text, "canonical: a fixed point"
    data = yaml.safe_load(text)["datasources"]["shop"]
    assert list(data["tables"]) == sorted(data["tables"]), "mappings are sorted by name"
    assert [t["term"] for t in data["terms"]] == ["revenue", "payroll", "cost"], "lists keep their order"
    assert "from" in data["joins"][0] and "from_" not in data["joins"][0]

    path = _write_glossary(tmp_path)
    new_sha = write_glossary(path, glossary, file_sha256(path))
    assert path.read_text(encoding="utf-8") == text
    assert new_sha == file_sha256(path)


def test_write_refuses_a_changed_file(tmp_path):
    path = _write_glossary(tmp_path)
    expected = file_sha256(path)
    path.write_text(path.read_text(encoding="utf-8") + "# edited by hand\n", encoding="utf-8")

    with pytest.raises(ReviewError) as caught:
        write_glossary(path, Glossary(version=1), expected)
    assert caught.value.code is ReviewErrorCode.FILE_CHANGED
    assert "edited by hand" in path.read_text(encoding="utf-8"), "nothing was written"


def test_write_goes_through_a_symlink(tmp_path):
    real = _write_glossary(tmp_path, name="real.yaml")
    link = tmp_path / "glossary.yaml"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")

    write_glossary(link, Glossary(version=1), file_sha256(link))
    assert link.is_symlink(), "the link is kept"
    assert yaml.safe_load(real.read_text(encoding="utf-8")) == {"version": 1, "datasources": {}}


# ---------------------------------------------------------------- merging


def _loaded(label: str, data: dict) -> LoadedSource:
    return LoadedSource(type=label, glossary=Glossary.model_validate(data), sha256=None, path=None,
                        loaded_at=datetime.now(timezone.utc))


def test_first_source_wins_a_description_and_lists_union():
    first = {"version": 1, "datasources": {"shop": {
        "tables": {"orders": {"description": "First."}},
        "columns": {"orders.total": {"synonyms": ["amount"]}},
        "terms": [{"term": "revenue", "definition": "First."}],
        "rules": [{"id": "r1", "text": "First."}],
        "examples": ["One?"]}}}
    second = {"version": 1, "datasources": {"shop": {
        "tables": {"Orders": {"description": "Second.", "notes": "Only the second has notes."}},
        "columns": {"orders.total": {"synonyms": ["Amount", "sum"], "role": "money"}},
        "terms": [{"term": "Revenue", "definition": "Second."}, {"term": "margin", "definition": "Second."}],
        "rules": [{"id": "r1", "text": "Second."}, {"id": "r2", "text": "Second."}],
        "examples": ["One?", "Two?"]}}}

    knowledge = merge([_loaded("yaml", first), _loaded("wiki", second)])
    shop = knowledge.datasources["shop"]

    assert shop.tables["orders"].description == "First."
    assert shop.tables["orders"].notes == "Only the second has notes."
    assert shop.columns["orders.total"].synonyms == ["amount", "sum"]
    assert shop.columns["orders.total"].role == "money"
    assert [(t.term, t.definition) for t in shop.terms] == [("revenue", "First."), ("margin", "Second.")]
    assert [(r.id, r.text) for r in shop.rules] == [("r1", "First."), ("r2", "Second.")]
    assert shop.examples == ["One?", "Two?"]
    provenance = knowledge.provenance["shop"]
    assert provenance["tables.orders.description"] == "yaml"
    assert provenance["tables.orders.notes"] == "wiki"
    assert provenance["terms.margin"] == "wiki"


def test_a_later_sources_code_values_do_not_outlive_an_earlier_role():
    first = {"version": 1, "datasources": {"shop": {"columns": {"orders.status": {"role": "text"}}}}}
    second = {"version": 1, "datasources": {"shop": {"columns": {"orders.status": {
        "role": "enum", "values": {"1": "open"}}}}}}

    column = merge([_loaded("yaml", first), _loaded("wiki", second)]).datasources["shop"].columns["orders.status"]
    assert (column.role, column.values) == ("text", {})


def test_the_digest_follows_the_merged_knowledge():
    one = merge([_loaded("yaml", GLOSSARY)])
    same = merge([_loaded("yaml", GLOSSARY)])
    changed = {**GLOSSARY, "datasources": {"shop": {**GLOSSARY["datasources"]["shop"], "examples": []}}}

    assert one.digests["shop"] == same.digests["shop"]
    assert len(one.digests["shop"]) == 64
    assert merge([_loaded("yaml", changed)]).digests["shop"] != one.digests["shop"]
    assert one.digest("other") == merge([]).digest("other"), "no knowledge has one digest"


# ---------------------------------------------------------------- resolution and derived roles


def test_key_and_temporal_columns_get_derived_roles():
    fk = ForeignKeyContract(constrained_columns=["customer_id"],
                            referred_table=TableRef(schema_name="main", table_name="customers"),
                            referred_columns=["id"])
    snapshot = _snapshot({"customers": {"id": "INTEGER pk", "name": "TEXT"},
                          "orders": {"id": "INTEGER pk", "customer_id": "INTEGER", "created_at": "TIMESTAMP",
                                     "total": "REAL"}}, foreign_keys={"orders": [fk]})

    roles = derived_roles(snapshot, dialect="sqlite")
    assert roles == {"customers.id": "identifier", "orders.id": "identifier", "orders.customer_id": "identifier",
                     "orders.created_at": "date"}


def test_glossary_overrides_a_derived_role(engine):
    shop = engine.knowledge.show("shop", user_context=ADMIN)[0]

    assert shop.knowledge.columns["orders.id"].role == "quantity"
    assert shop.origins["columns.orders.id.role"] == "knowledge"
    assert shop.knowledge.columns["customers.id"].role == "identifier"
    assert shop.origins["columns.customers.id.role"] == "derived"
    assert shop.knowledge.columns["orders.created_at"].role == "date"


def test_unresolved_name_is_a_problem_not_an_error(engine):
    data = {"version": 1, "datasources": {"shop": {
        "tables": {"ordrs": {"description": "A typo."}},
        "columns": {"orders.totl": {"description": "A typo."}},
        "terms": [{"term": "revenue", "columns": ["orders.nope"]}]}}}
    knowledge = DatasourceKnowledge.model_validate(data["datasources"]["shop"])

    found = problems(knowledge, SHOP_SNAPSHOT, dialect="sqlite")
    assert any("ordrs" in p for p in found)
    assert any("orders.totl" in p for p in found)
    assert any("orders.nope" in p and "revenue" in p for p in found)


def test_ambiguous_bare_name_is_a_problem():
    snapshot = _snapshot({"sales.orders": {"id": "INTEGER pk"}, "archive.orders": {"id": "INTEGER pk"}})
    knowledge = DatasourceKnowledge.model_validate({"tables": {"orders": {"description": "Which?"},
                                                               "sales.orders": {"description": "This one."}}})

    found = problems(knowledge, snapshot, dialect=None)
    assert len(found) == 1
    assert "orders" in found[0] and "schema.table" in found[0]


def test_uncomparable_join_types_are_a_problem():
    knowledge = DatasourceKnowledge.model_validate({"joins": [
        {"from": "orders.created_at", "to": "customers.id"},
        {"from": "orders.customer_id", "to": "customers.id"}]})

    found = problems(knowledge, SHOP_SNAPSHOT, dialect="sqlite")
    assert len(found) == 1
    assert "orders.created_at" in found[0] and "a date or time" in found[0] and "a number" in found[0]


def test_engine_show_lists_problems(tmp_path):
    data = {"version": 1, "datasources": {"shop": {"tables": {"ordrs": {"description": "A typo."}}}}}
    _write_glossary(tmp_path, data)
    with _engine(tmp_path) as engine:
        assert engine.index.build().ok
        shop = engine.knowledge.show("shop", user_context=ADMIN)[0]
    assert any("ordrs" in p for p in shop.problems)


# ---------------------------------------------------------------- the scope filter


def test_show_requires_a_caller(engine):
    parameter = inspect.signature(engine.knowledge.show).parameters["user_context"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is inspect.Parameter.empty
    for method in ("sources", "show", "export", "reload"):
        with pytest.raises(TypeError):
            getattr(engine.knowledge, method)()
        with pytest.raises(TypeError):
            getattr(engine.knowledge, method)(user_context=None)


def test_show_hides_items_on_or_mentioning_denied_tables(engine):
    admin = engine.knowledge.show("shop", user_context=ADMIN)[0]
    analyst = engine.knowledge.show("shop", user_context=ANALYST)[0]

    assert set(admin.knowledge.tables) >= {"orders", "salaries", "customers"}
    k = analyst.knowledge
    assert "salaries" not in k.tables, "on a denied table"
    assert k.tables.get("customers") is None or k.tables["customers"].description is None, \
        "a description naming a denied table is not shown"
    assert "salaries.amount" not in k.columns
    assert [(j.from_, j.to) for j in k.joins] == [("orders.customer_id", "customers.id")]
    assert [t.term for t in k.terms] == ["revenue"], "payroll names the table; cost mentions it in prose"
    assert [r.id for r in k.rules] == ["no_test"], "pay_private mentions SALARIES, case ignored"
    assert k.examples == ["What is revenue by month?"]
    rendered = analyst.model_dump_json().lower()
    assert "salaries" not in rendered, "nothing names the denied table"


def test_a_caller_who_cannot_reach_the_datasource_sees_nothing(engine):
    assert engine.knowledge.show(user_context=NOBODY) == []
    assert engine.knowledge.export(user_context=NOBODY).strip().endswith("datasources: {}")
    assert all(not info.counts for info in engine.knowledge.sources(user_context=NOBODY))


def test_an_unindexed_datasource_shows_nothing_but_the_reason(tmp_path):
    with _engine(tmp_path) as engine:
        shop = engine.knowledge.show("shop", user_context=ADMIN)[0]
    assert shop.knowledge == DatasourceKnowledge()
    assert shop.problems and "index" in shop.problems[0]


def test_an_unknown_datasource_is_an_error(engine):
    with pytest.raises(ValueError, match="nope"):
        engine.knowledge.show("nope", user_context=ADMIN)


def test_scope_filter_by_names_and_mentions():
    knowledge = DatasourceKnowledge.model_validate(GLOSSARY["datasources"]["shop"])
    allows = lambda schema, table: table.lower() != "salaries"  # noqa: E731
    denied = denied_table_names(SHOP_SNAPSHOT, allows)

    assert denied == {"salaries"}
    shown = visible(knowledge, SHOP_SNAPSHOT, allows)
    assert "salaries" not in shown.model_dump_json().lower()
    assert [t.term for t in shown.terms] == ["revenue"]


def test_a_mention_is_a_whole_word():
    knowledge = DatasourceKnowledge.model_validate({"rules": [
        {"id": "a", "text": "Uses salaries_history, not the live table."},
        {"id": "b", "text": "Never join salaries."}]})
    allows = lambda schema, table: table != "salaries"  # noqa: E731

    assert [r.id for r in visible(knowledge, SHOP_SNAPSHOT, allows).rules] == ["a"]


# ---------------------------------------------------------------- sources, export, reload


def test_sources_report_each_source(engine, tmp_path):
    info = engine.knowledge.sources(user_context=ADMIN)
    assert [i.type for i in info] == ["yaml"]
    assert info[0].sha256 == file_sha256(tmp_path / "glossary.yaml")
    assert info[0].loaded_at is not None and info[0].error is None
    assert info[0].counts["shop"]["terms"] == 3
    assert engine.knowledge.sources(user_context=ANALYST)[0].counts["shop"]["terms"] == 1


def test_export_is_the_visible_knowledge_as_a_canonical_glossary(engine, tmp_path):
    text = engine.knowledge.export(user_context=ANALYST)

    glossary = Glossary.model_validate(yaml.safe_load(text))
    assert "salaries" not in text.lower()
    shop = glossary.datasources["shop"]
    assert [t.term for t in shop.terms] == ["revenue"]
    assert "customers.id" not in shop.columns, "derived roles are not exported"
    assert canonical_yaml(glossary) == text

    out = tmp_path / "exported.yaml"
    assert engine.knowledge.export(out, datasource_ids=["shop"], user_context=ADMIN) == out.read_text(
        encoding="utf-8")


def test_export_refuses_to_overwrite_a_config_or_source_file(engine, tmp_path):
    for target in ("glossary.yaml", "nl2sql.yaml"):
        before = (tmp_path / target).read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="overwrite"):
            engine.knowledge.export(tmp_path / target, user_context=ADMIN)
        assert (tmp_path / target).read_text(encoding="utf-8") == before


def test_reload_needs_an_admin_role(engine):
    with pytest.raises(PermissionError):
        engine.knowledge.reload(user_context=ANALYST)


def test_reload_swaps_atomically_and_keeps_old_on_error(engine, tmp_path):
    seen: List = []
    engine.events.subscribe(seen.append, names=["knowledge.loaded"])
    changed = {"version": 1, "datasources": {"shop": {"terms": [{"term": "margin", "definition": "New."}]}}}
    _write_glossary(tmp_path, changed)

    info = engine.knowledge.reload(user_context=ADMIN)
    assert info[0].error is None
    assert [t.term for t in engine.knowledge.show("shop", user_context=ADMIN)[0].knowledge.terms] == ["margin"]
    assert seen[-1].data["error"] is None and "shop" in seen[-1].data["digests"]

    (tmp_path / "glossary.yaml").write_text("version: 2\n", encoding="utf-8")
    info = engine.knowledge.reload(user_context=ADMIN)
    assert info[0].error and "version" in info[0].error
    assert [t.term for t in engine.knowledge.show("shop", user_context=ADMIN)[0].knowledge.terms] == ["margin"], \
        "the previous knowledge stays live"
    assert seen[-1].data["error"]


def test_knowledge_loaded_is_published_at_build(tmp_path):
    seen: List = []

    class Recorder:
        events = ["knowledge.loaded"]

        def on_event(self, event):
            seen.append(event)

    with _engine(tmp_path) as built:  # writes the database and the glossary
        cfg = built.config.model_copy(update={"observers": [Recorder()]})
    with NL2SQL(cfg):
        pass
    assert [e.name for e in seen] == ["knowledge.loaded"]
    assert seen[0].data["sources"][0]["type"] == "yaml" and seen[0].data["error"] is None

    seen.clear()
    with NL2SQL(cfg.model_copy(update={"knowledge": []})):
        pass
    assert seen == [], "no knowledge section: nothing to report"


def test_run_pins_the_knowledge_it_started_with(engine, tmp_path):
    runtime = engine._runtime
    pinned = runtime.pin_index()
    before = pinned.knowledge
    _write_glossary(tmp_path, {"version": 1, "datasources": {}})

    engine.knowledge.reload(user_context=ADMIN)
    assert runtime.knowledge is not before
    assert pinned.knowledge is before, "a run keeps the knowledge it started with"


def test_no_knowledge_section_means_no_knowledge(tmp_path):
    with _engine(tmp_path, knowledge=[]) as engine:
        assert engine.knowledge.sources(user_context=ADMIN) == []
        assert engine.index.build().ok
        assert engine.knowledge.show("shop", user_context=ADMIN)[0].knowledge.terms == []


def test_a_knowledge_source_object_is_used_as_given(tmp_path):
    class Wiki:
        type = "wiki"

        def load(self):
            return Glossary.model_validate({"version": 1, "datasources": {"shop": {
                "terms": [{"term": "churn", "definition": "From the wiki."}]}}})

    with _engine(tmp_path, knowledge=[]) as built:
        cfg = built.config.model_copy(update={"knowledge": [Wiki()]})
    with NL2SQL(cfg) as engine:
        assert engine.index.build().ok
        view = engine.knowledge.show("shop", user_context=ADMIN)[0]
    assert [t.term for t in view.knowledge.terms] == ["churn"]
    assert view.sources["terms.churn"] == "wiki"


# ---------------------------------------------------------------- the appliers


def test_appliers_merge_each_kind_into_a_glossary():
    glossary = Glossary.model_validate(GLOSSARY)

    glossary = apply_change(glossary, "table", "shop", "orders", {"description": "Changed."})
    glossary = apply_change(glossary, "column", "shop", "orders.created_at", {"grain": "day"})
    glossary = apply_change(glossary, "join", "shop", "orders.customer_id->customers.id",
                            {"from": "orders.customer_id", "to": "customers.id", "cardinality": "one-to-one"})
    glossary = apply_change(glossary, "term", "shop", "Revenue", {"term": "Revenue", "definition": "Replaced."})
    glossary = apply_change(glossary, "term", "new_ds", "churn", {"term": "churn"})

    shop = glossary.datasources["shop"]
    assert shop.tables["orders"].description == "Changed."
    assert shop.tables["orders"].notes == "Kept for seven years.", "a change sets only its own fields"
    assert shop.columns["orders.created_at"].grain == "day"
    assert [(j.from_, j.cardinality) for j in shop.joins][0] == ("orders.customer_id", "one-to-one")
    assert len(shop.joins) == 2, "the same join is replaced, not added"
    assert [(t.term, t.definition) for t in shop.terms][0] == ("Revenue", "Replaced.")
    assert len(shop.terms) == 3, "a term whose name exists is replaced (D50)"
    assert glossary.datasources["new_ds"].terms[0].term == "churn"


def test_an_applied_change_is_validated():
    glossary = Glossary(version=1)
    with pytest.raises(ValueError):
        apply_change(glossary, "column", "shop", "orders.total", {"values": {"1": "x"}})
    with pytest.raises(ValueError):
        apply_change(glossary, "table", "shop", "orders", {"description": "x" * 20}, item_max_chars=10)
    with pytest.raises(ValueError, match="verified_query"):
        apply_change(glossary, "verified_query", "shop", "q", {})


# ---------------------------------------------------------------- the demo glossary


def test_the_demo_glossary_is_canonical_and_resolves_against_chinook(tmp_path):
    from nl2sql.datasets import DEMO_GLOSSARY_PATH
    from nl2sql.datasets.chinook import CHINOOK_DATASOURCE

    text = DEMO_GLOSSARY_PATH.read_text(encoding="utf-8")
    glossary = Glossary.model_validate(yaml.safe_load(text))
    assert canonical_yaml(glossary) == text
    shop = glossary.datasources["chinook"]
    sales = [t.term for t in shop.terms if "sales" in t.synonyms]
    assert len(sales) == 2, "the demo carries one real ambiguity"

    from nl2sql.datasets import CHINOOK_DB_PATH

    chinook = {**CHINOOK_DATASOURCE, "connection": {"database": CHINOOK_DB_PATH.as_posix()}}
    cfg = make_config(tmp_path, datasources=[chinook],
                      knowledge=[{"type": "yaml", "path": DEMO_GLOSSARY_PATH.as_posix()}])
    with NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                  dimension=8)})) as engine:
        assert engine.index.build().ok
        view = engine.knowledge.show("chinook", user_context=UserContext())
    assert view[0].problems == []


def test_registry_default_finds_the_yaml_source():
    assert "yaml" in Registry.default().names("knowledge_source")
