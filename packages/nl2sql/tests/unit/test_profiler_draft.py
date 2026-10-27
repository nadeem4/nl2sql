"""Drafted descriptions: ``engine.knowledge.profile(draft=True)`` and ``nl2sql profile --draft`` (P2).

docs/architecture/wave_b.md, section 6 ("Drafted descriptions"), decisions D38, D44, D60 and B31. The model is a
``FakeLLMServer`` answering the profiler's structured output (``SchemaEnrichment``) from the evidence in the
prompt, so nothing calls a real model. The database is a small SQLite file indexed without embedding.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding
from typer.testing import CliRunner

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.cli.main import app
from nl2sql.evaluation.run import UnknownPriceError
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql_sdk.stores import BuildPointer

MODEL = "gpt-profiler-test"  # priced by llm.prices only: $1 per million tokens in and out
ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "reader": {"description": "Customers only", "allow": ["shop.main.customers"]}}
ADMIN = UserContext(user_id="ada", roles=["admin"])
READER = UserContext(user_id="rae", roles=["reader"])
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MARK = "Evidence (JSON):"
runner = CliRunner()


class _Drafter:
    """The fake model: describes every table and column the prompt's evidence asks for."""

    def __init__(self):
        self.prompts = []
        self.asked = []
        self.rule = Rule("SchemaEnrichment", self._answer)
        self.server = FakeLLMServer([self.rule])

    def _answer(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        evidence, _ = json.JSONDecoder().raw_decode(prompt[prompt.index(MARK) + len(MARK):].lstrip())
        ask = evidence["describe"]
        self.asked.append(ask)
        return {"datasource": {},
                "tables": {t: {"description": f"Drafted {t}"} for t in ask["tables"]},
                "columns": {c: {"description": f"Drafted {c}", "synonyms": [c.split(".")[-1] + " alias"]}
                            for c in ask["columns"]}}

    def usage(self, tokens: int) -> None:
        self.rule.usage = {"prompt_tokens": tokens, "completion_tokens": 1, "total_tokens": tokens + 1}


@pytest.fixture
def drafter():
    d = _Drafter()
    d.server.start()
    yield d
    d.server.stop()


def _database(folder: Path, fk: bool = False) -> None:
    if (folder / "shop.db").exists():
        return
    link = ", secret_ledger_id INTEGER REFERENCES orders(order_id)" if fk else ""
    with sqlite3.connect(folder / "shop.db") as connection:
        connection.executescript(f"""
            CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT{link});
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, total NUMERIC, status TEXT);
            INSERT INTO customers (customer_id, name) VALUES (1, 'a'), (2, 'b'), (3, 'c');
            INSERT INTO orders VALUES (1, 1, 5.0, 'new'), (2, 2, 7.5, 'paid'), (3, 3, 9.0, 'new');
        """)
    connection.close()


def _engine(folder: Path, drafter: Optional[_Drafter] = None, *, comments: bool = False, glossary: Optional[dict] = None,
            priced: bool = True, settings: Optional[dict] = None, as_cli: bool = False,
            fk: bool = False) -> NL2SQL:
    _database(folder, fk)
    sections: dict = {}
    if drafter is not None:
        sections["llm"] = {"provider": "openai", "model": MODEL, "api_key": "sk-fake-profiler",
                           "base_url": drafter.server.base_url,
                           "prices": {MODEL: {"input": 1.0, "output": 1.0}} if priced else {}}
    if settings:
        sections["settings"] = settings
    if glossary is not None:
        (folder / "glossary.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": {"shop": glossary}}),
                                              encoding="utf-8")
        sections["knowledge"] = [{"type": "yaml", "path": "glossary.yaml"}]
    config = make_config(folder, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                         policies={"roles": ROLES}, **sections)
    dimension = 384 if as_cli else 8  # the CLI builds its own engine from nl2sql.yaml: same index namespace
    if not as_cli:
        config = config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                    dimension=8)})
    engine = NL2SQL(config)
    ctx = engine._runtime
    snapshot = ctx.ds_registry.get_adapter("shop").fetch_schema_snapshot(ctx.ds_registry.get_index_scope("shop"))
    if comments:  # SQLite has no comments: the database's own text is put on the snapshot
        snapshot.metadata.tables[_key(snapshot, "customers")].description = "From the database"
        snapshot.metadata.tables[_key(snapshot, "orders")].columns["status"].description = "Database says status"
    version = ctx.state.put_snapshot(snapshot)
    ctx.index.open(ctx.namespace, dimension)
    ctx.index.set_pointers(ctx.namespace, {"shop": BuildPointer(
        build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})
    return engine


def _key(snapshot, table: str) -> str:
    return next(k for k in snapshot.contract.tables if k.endswith(f".[{table}]"))


def _rows(engine, kind) -> Dict[str, object]:
    return {p.target: p for p in engine.proposals.list(user_context=ADMIN, kind=kind)}


def test_draft_files_table_and_column_descriptions_as_proposals(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN, draft=True)

        tables, columns = _rows(engine, "table"), _rows(engine, "column")
        assert sorted(tables) == ["customers", "orders"]
        assert tables["customers"].change == {"description": "Drafted [main].[customers]"}
        assert tables["customers"].origin == "draft" and tables["customers"].status == "proposed"
        assert tables["orders"].tables == ["shop.main.orders"]
        assert columns["orders.status"].change["description"] == "Drafted [main].[orders].status"
        assert columns["orders.status"].change["synonyms"] == ["status alias"]
        assert report.drafted == 2 + 6 and report.stopped_by == "done" and report.issues == []
        assert report.cost is not None and report.cost > 0


def test_draft_never_overwrites_glossary_or_database_text(tmp_path, drafter):
    glossary = {"tables": {"orders": {"description": "Glossary orders"}},
                "columns": {"customers.name": {"description": "The glossary says"}}}
    with _engine(tmp_path, drafter, comments=True, glossary=glossary) as engine:
        engine.knowledge.profile(user_context=ADMIN, draft=True)

        tables, columns = _rows(engine, "table"), _rows(engine, "column")
        assert tables == {}  # customers has the database's text, orders the glossary's
        for said in ("customers.name", "orders.status"):  # the glossary's text, the database's text
            assert said in columns and "description" not in columns[said].change
        assert columns["orders.total"].change["description"] == "Drafted [main].[orders].total"
        asked_columns = {c for ask in drafter.asked for c in ask["columns"]}
        assert "[main].[orders].status" not in asked_columns and "[main].[customers].name" not in asked_columns
        assert not any(ask["tables"] for ask in drafter.asked)


def test_draft_reaches_no_prompt_until_accepted(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        engine.knowledge.profile(user_context=ADMIN, draft=True)

        [view] = engine.knowledge.show("shop", user_context=ADMIN)
        assert "Drafted" not in view.knowledge.model_dump_json()
        snapshot = engine._runtime.vector_store.snapshot("shop")
        assert "Drafted" not in snapshot.metadata.model_dump_json()
        assert "Drafted" not in snapshot.contract.model_dump_json()


def test_accepted_draft_moves_to_the_glossary(tmp_path, drafter):
    with _engine(tmp_path, drafter, glossary={}) as engine:
        engine.knowledge.profile(user_context=ADMIN, draft=True)
        row = _rows(engine, "table")["customers"]

        report = engine.proposals.accept(row.id, by=ADMIN)

        assert not report.refused
        written = yaml.safe_load((tmp_path / "glossary.yaml").read_text(encoding="utf-8"))
        assert written["datasources"]["shop"]["tables"]["customers"]["description"] == "Drafted [main].[customers]"
        [view] = engine.knowledge.show("shop", user_context=ADMIN)
        assert "Drafted [main].[customers]" in view.knowledge.model_dump_json()


def test_one_column_proposal_carries_both_role_and_description(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        engine.knowledge.profile(user_context=ADMIN, draft=True)

        every = [p for p in engine.proposals.list(user_context=ADMIN, kind="column", status=None)]
        total = [p for p in every if p.target == "orders.total"]
        assert len(total) == 1 and total[0].status == "proposed"  # the description did not supersede the role
        assert total[0].change == {"role": "money", "description": "Drafted [main].[orders].total",
                                   "synonyms": ["total alias"]}
        assert total[0].origin == "draft"
        assert len({p.target for p in every}) == len(every)  # one proposal per column
        assert not [p for p in every if p.status == "superseded"]


def test_a_plain_profile_files_roles_only(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN)

        assert drafter.prompts == [] and report.drafted == 0 and report.cost is None
        assert _rows(engine, "column")["orders.total"].change == {"role": "money"}
        assert _rows(engine, "column")["orders.total"].origin == "profiler"
        assert _rows(engine, "table") == {}


def test_draft_stops_at_its_cost_cap(tmp_path, drafter):
    drafter.usage(1_000_000)  # one call spends $1 of the $0.50 cap
    with _engine(tmp_path, drafter) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN, draft=True, max_cost_usd=0.5)

        assert len(drafter.server.calls) == 1  # the second table's call is refused before it is sent
        assert report.stopped_by == "cost_cap" and report.drafted > 0 and report.cost == pytest.approx(1.000001)
        assert len(_rows(engine, "table")) == 1  # what was drafted so far is filed
        assert any("cost limit" in issue for issue in report.issues)


def test_draft_takes_its_cost_cap_from_settings(tmp_path, drafter):
    drafter.usage(1_000_000)
    with _engine(tmp_path, drafter, settings={"profiler": {"max_cost_usd": 0.25}}) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN, draft=True)
        assert report.stopped_by == "cost_cap" and len(drafter.server.calls) == 1


def test_draft_refuses_an_unpriced_model_before_any_call(tmp_path, drafter):
    with _engine(tmp_path, drafter, priced=False) as engine:
        with pytest.raises(UnknownPriceError, match=MODEL):
            engine.knowledge.profile(user_context=ADMIN, draft=True)
        assert drafter.server.calls == []
        assert engine.proposals.list(user_context=ADMIN) == []  # nothing, not even the roles


def test_draft_without_an_llm_reports_and_drafts_nothing(tmp_path):
    with _engine(tmp_path) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN, draft=True)

        assert report.drafted == 0 and report.cost is None
        assert any("profiler" in issue and "model" in issue for issue in report.issues)
        assert _rows(engine, "table") == {} and "orders.total" in _rows(engine, "column")  # the roles still come


def test_a_nonpositive_cap_is_a_value_error(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        with pytest.raises(ValueError, match="max_cost_usd"):
            engine.knowledge.profile(user_context=ADMIN, draft=True, max_cost_usd=0)


def test_a_denied_table_is_never_shown_to_the_model(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        engine.knowledge.profile(user_context=READER, draft=True)

        assert drafter.prompts and all("orders" not in p for p in drafter.prompts)
        assert sorted(_rows(engine, "table")) == ["customers"]


def test_the_cli_drafts_under_a_cost_cap(tmp_path, drafter):
    with _engine(tmp_path, drafter, as_cli=True):
        pass
    result = runner.invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "profile", "--as", "admin", "--draft",
                                 "--max-cost", "0.25"])
    out = ANSI.sub("", result.stdout)
    assert result.exit_code == 0, out
    assert "descriptions drafted" in out and "Waiting for review" in out and "Drafted [main].[customers]" in out


def test_a_foreign_key_to_a_denied_table_does_not_reach_the_prompt(tmp_path, drafter):
    with _engine(tmp_path, drafter, fk=True) as engine:
        engine.knowledge.profile(user_context=READER, draft=True)

        assert drafter.prompts and all("orders" not in p and "order_id" not in p for p in drafter.prompts)


def test_a_plain_profile_keeps_an_open_draft(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        engine.knowledge.profile(user_context=ADMIN, draft=True)
        engine.knowledge.profile(user_context=ADMIN)

        row = _rows(engine, "column")["orders.total"]
        assert row.change["role"] == "money" and row.change["description"] and row.status == "proposed"


def test_a_repeat_draft_does_not_ask_again_for_what_waits_in_the_queue(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        engine.knowledge.profile(user_context=ADMIN, draft=True)
        before = {p.id for p in engine.proposals.list(user_context=ADMIN)}
        calls = len(drafter.server.calls)

        [report] = engine.knowledge.profile(user_context=ADMIN, draft=True)

        assert len(drafter.server.calls) == calls and report.drafted == 0
        assert {p.id for p in engine.proposals.list(user_context=ADMIN)} == before


def test_a_cap_without_draft_is_a_value_error(tmp_path, drafter):
    with _engine(tmp_path, drafter) as engine:
        with pytest.raises(ValueError, match="draft"):
            engine.knowledge.profile(user_context=ADMIN, max_cost_usd=1.0)


def test_the_cli_refuses_a_cap_without_draft(tmp_path, drafter):
    with _engine(tmp_path, drafter, as_cli=True):
        pass
    result = runner.invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "profile", "--as", "admin",
                                 "--max-cost", "1"])
    assert result.exit_code == 1 and "--draft" in ANSI.sub("", result.stdout)
