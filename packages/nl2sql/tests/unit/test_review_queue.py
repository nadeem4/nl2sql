"""The review queue: proposals, accept, reject, the review file and thumbs-up (Q1).

docs/architecture/wave_b.md, section 5, and decisions D41, D42, D43, D60 and
D61. The engine tests build a real engine on a small SQLite database with a
fake embedder, so nothing is downloaded and no model is called.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding
from typer.testing import CliRunner

from nl2sql import NL2SQL, AcceptReport, Embeddings, ReviewError, UserContext
from nl2sql.indexing.retrieval import CURATED_TYPE
from nl2sql.review.models import ReviewErrorCode
from nl2sql.review.queue import propose
from nl2sql.testing.config import make_config
from nl2sql_sdk.knowledge import Glossary
from nl2sql_sdk.stores import Filter, Proposal

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "All but pay", "allow": ["shop"], "deny": ["shop.main.salaries"]}}
ADMIN = UserContext(user_id="ada", roles=["admin"])
ANALYST = UserContext(user_id="bob", roles=["analyst"])

CURATED = {"datasources": {"shop": {"verified_queries": [{
    "id": "order_count", "description": "How many orders there are.",
    "questions": ["How many orders are there?"], "sql": "SELECT COUNT(*) AS n FROM orders"}]}}}
GLOSSARY = {"version": 1, "datasources": {"shop": {"tables": {"orders": {"description": "One row per order."}}}}}
SQL = "SELECT SUM(total) AS revenue FROM orders"


def _database(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "shop.db").exists():
        return
    with sqlite3.connect(folder / "shop.db") as connection:
        for statement in (
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL)",
            "CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount REAL)",
        ):
            connection.execute(statement)
    connection.close()


def _project(folder: Path, *, curated="file", knowledge=True, **sections):
    """Write the project; ``curated`` is ``"file"``, ``"inline"`` or None."""
    _database(folder)
    if not (folder / "curated.yaml").exists():
        (folder / "curated.yaml").write_text(yaml.safe_dump(CURATED, sort_keys=False), encoding="utf-8")
    if not (folder / "glossary.yaml").exists():
        (folder / "glossary.yaml").write_text(yaml.safe_dump(GLOSSARY, sort_keys=False), encoding="utf-8")
    if curated == "file":
        sections["curated"] = {"file": "curated.yaml"}
    elif curated == "inline":
        sections["curated"] = CURATED
    if knowledge:
        sections.setdefault("knowledge", [{"type": "yaml", "path": "glossary.yaml"}])
    return make_config(folder, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                       policies=sections.pop("policies", {"roles": ROLES}), **sections)


def _engine(config) -> NL2SQL:
    return NL2SQL(config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                       dimension=8)}))


@pytest.fixture
def engine(tmp_path):
    with _engine(_project(tmp_path)) as engine:
        assert engine.index.build().ok
        yield engine


def _result(trace_id="t1", sql=SQL, datasource="shop", status="success", source="generated"):
    return {"trace_id": trace_id, "status": status, "source": {"type": source},
            "sub_queries": [{"sql": sql, "datasource_id": datasource}]}


def _thumbs_up(engine, question="What is the revenue?", **result):
    engine.feedback.record(_result(**result), question=question, role="admin", rating="up")
    [proposal] = [p for p in engine.proposals.list(user_context=ADMIN, kind="verified_query")
                  if p.target == question]
    return proposal


def _proposal(engine, kind="column", target="orders.total", change=None, tables=("shop.main.orders",),
              origin="profiler"):
    proposal = Proposal.build(kind=kind, datasource_id="shop", target=target,
                              change=change if change is not None else {"description": "Order total in USD."},
                              origin=origin, tables=tables)
    return propose(engine._runtime, proposal)


def _verified(engine, qid="top", sql="SELECT id FROM customers", question="Who are the customers?"):
    return _proposal(engine, kind="verified_query", target=question, tables=("shop.main.customers",),
                     origin="correction",
                     change={"id": qid, "description": "Customers.", "questions": [question], "sql": sql})


def _curated_file(folder: Path) -> dict:
    return yaml.safe_load((folder / "curated.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- accept


def test_accept_needs_an_admin_role(engine, tmp_path):
    proposal = _thumbs_up(engine)
    before = (tmp_path / "curated.yaml").read_bytes()

    with pytest.raises(ReviewError) as error:
        engine.proposals.accept(proposal.id, by=ANALYST)

    assert error.value.code == ReviewErrorCode.NOT_AUTHORIZED
    assert (tmp_path / "curated.yaml").read_bytes() == before
    assert engine.proposals.get(proposal.id, user_context=ADMIN).status == "proposed"
    with pytest.raises(TypeError):
        engine.proposals.accept(proposal.id, by=None)


def test_accept_verified_query_writes_curated_yaml_and_refreshes(engine, tmp_path):
    proposal = _thumbs_up(engine)
    decided = []
    engine.events.subscribe(decided.append, names=["proposal.decided"])

    report = engine.proposals.accept(proposal.id, by=ADMIN, edits={proposal.id: {"description": "Total revenue."}})

    assert isinstance(report, AcceptReport) and report.refused == {}
    assert report.files_written == ["curated.yaml"]
    [accepted] = report.accepted
    assert accepted.status == "accepted" and accepted.decided_by == "ada" and accepted.wrote == "curated.yaml"
    queries = {q["id"]: q for q in _curated_file(tmp_path)["datasources"]["shop"]["verified_queries"]}
    assert set(queries) == {"order_count", "what_is_the_revenue"}
    assert queries["what_is_the_revenue"]["description"] == "Total revenue."
    assert queries["what_is_the_revenue"]["sql"].strip() == SQL
    assert (tmp_path / "curated.yaml").read_text(encoding="utf-8").startswith("#")  # the canonical header
    # The runtime answers from the new section, and the index has its entry.
    ctx = engine._runtime
    assert [q.id for q in ctx.curated.datasources["shop"].verified_queries] == ["order_count", "what_is_the_revenue"]
    assert ctx.config.curated == ctx.curated
    entries = ctx.index.get(ctx.namespace, Filter(eq={"type": CURATED_TYPE}))
    assert any("What is the revenue?" in e.text for e in entries)
    assert [e.data["decision"] for e in decided] == ["accepted"] and decided[0].data["wrote"] == "curated.yaml"
    # A second accept of the same id is refused: it is no longer proposed.
    again = engine.proposals.accept(proposal.id, by=ADMIN)
    assert proposal.id in again.refused and again.files_written == []


def test_accept_runs_the_curated_checks_first(engine, tmp_path):
    bad = _verified(engine, sql="DELETE FROM customers")
    clash = _verified(engine, qid="order_count", question="Count the orders")
    before = (tmp_path / "curated.yaml").read_bytes()

    report = engine.proposals.accept([bad.id, clash.id], by=ADMIN)

    assert set(report.refused) == {bad.id, clash.id}
    assert "read-only" in report.refused[bad.id].lower() or "select" in report.refused[bad.id].lower()
    assert "order_count" in report.refused[clash.id]
    assert report.files_written == [] and (tmp_path / "curated.yaml").read_bytes() == before
    assert engine.proposals.get(bad.id, user_context=ADMIN).status == "proposed"


def test_accept_refuses_an_inline_curated_section(tmp_path):
    with _engine(_project(tmp_path, curated="inline")) as engine:
        proposal = _verified(engine)
        with pytest.raises(ReviewError) as inline:
            engine.proposals.accept(proposal.id, by=ADMIN)
    assert inline.value.code == ReviewErrorCode.NOT_WRITABLE and "export" in str(inline.value)

    with _engine(_project(tmp_path / "none", curated=None)) as engine:
        proposal = _verified(engine)
        with pytest.raises(ReviewError) as missing:
            engine.proposals.accept(proposal.id, by=ADMIN)
    assert missing.value.code == ReviewErrorCode.NOT_WRITABLE
    assert "curated: {file: curated.yaml}" in str(missing.value)

    with _engine(_project(tmp_path / "noknow", knowledge=False)) as engine:
        proposal = _proposal(engine)
        with pytest.raises(ReviewError) as no_source:
            engine.proposals.accept(proposal.id, by=ADMIN)
    assert no_source.value.code == ReviewErrorCode.NOT_WRITABLE and "yaml" in str(no_source.value)


def test_accept_refuses_a_changed_file(engine, tmp_path):
    proposal = _thumbs_up(engine)
    (tmp_path / "curated.yaml").write_text(yaml.safe_dump(CURATED) + "# edited by hand\n", encoding="utf-8")

    with pytest.raises(ReviewError) as error:
        engine.proposals.accept(proposal.id, by=ADMIN)

    assert error.value.code == ReviewErrorCode.FILE_CHANGED
    assert "edited by hand" in (tmp_path / "curated.yaml").read_text(encoding="utf-8")
    assert engine.proposals.get(proposal.id, user_context=ADMIN).status == "proposed"


def test_concurrent_accepts_do_not_lose_a_write(engine, tmp_path):
    first, second = _verified(engine, qid="one", question="One?"), _verified(engine, qid="two", question="Two?")
    start = threading.Barrier(2)
    errors = []

    def accept(proposal_id):
        start.wait()
        try:
            engine.proposals.accept(proposal_id, by=ADMIN)
        except Exception as e:  # pragma: no cover - the assertion below names it
            errors.append(e)

    threads = [threading.Thread(target=accept, args=(p.id,)) for p in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    ids = {q["id"] for q in _curated_file(tmp_path)["datasources"]["shop"]["verified_queries"]}
    assert {"one", "two"} <= ids

    # A second engine on the same project loaded the file before the first wrote: it refuses rather than lose it.
    with _engine(_project(tmp_path)) as other:
        third = _verified(other, qid="three", question="Three?")
        four = _verified(engine, qid="four", question="Four?")
        engine.proposals.accept(four.id, by=ADMIN)
        with pytest.raises(ReviewError) as error:
            other.proposals.accept(third.id, by=ADMIN)
    assert error.value.code == ReviewErrorCode.FILE_CHANGED
    assert "four" in {q["id"] for q in _curated_file(tmp_path)["datasources"]["shop"]["verified_queries"]}


def test_accepting_many_writes_each_file_once(engine, tmp_path, monkeypatch):
    from nl2sql.review import accept as accept_module

    writes = []
    for name in ("write_curated", "write_glossary"):
        real = getattr(accept_module, name)
        monkeypatch.setattr(accept_module, name,
                            lambda *a, _real=real, _name=name, **k: (writes.append(_name), _real(*a, **k))[1])
    ids = [_verified(engine, qid=f"q{i}", question=f"Question {i}?").id for i in range(3)]
    ids += [_proposal(engine).id,
            _proposal(engine, kind="term", target="revenue", tables=(),
                      change={"term": "revenue", "definition": "Sum of orders.total."}).id]

    report = engine.proposals.accept(ids, by=ADMIN)

    assert report.refused == {} and len(report.accepted) == 5
    assert sorted(writes) == ["write_curated", "write_glossary"]
    assert sorted(report.files_written) == ["curated.yaml", "glossary.yaml"]
    glossary = Glossary.model_validate(yaml.safe_load((tmp_path / "glossary.yaml").read_text(encoding="utf-8")))
    assert glossary.datasources["shop"].columns["orders.total"].description == "Order total in USD."
    assert [t.term for t in glossary.datasources["shop"].terms] == ["revenue"]
    # The knowledge was reloaded: the next run's prompts have the items.
    assert engine._runtime.knowledge.get("shop").columns["orders.total"].description == "Order total in USD."


def test_accept_warns_when_a_source_shadows_it(tmp_path):
    class Catalog:
        type = "catalog"

        def load(self):
            return Glossary.model_validate({"version": 1, "datasources": {"shop": {
                "columns": {"orders.total": {"description": "From the catalog."}}}}})

    config = _project(tmp_path)
    config = config.model_copy(update={"knowledge": [Catalog(), *config.knowledge]})
    with _engine(config) as engine:
        proposal = _proposal(engine)
        report = engine.proposals.accept(proposal.id, by=ADMIN)

    assert report.files_written == ["glossary.yaml"]
    assert any("catalog" in w and "orders.total" in w for w in report.warnings), report.warnings


# ---------------------------------------------------------------- identity, supersede, visibility


def test_rejected_change_is_not_proposed_again(engine):
    proposal = _proposal(engine)
    [rejected] = engine.proposals.reject(proposal.id, by=ADMIN, reason="Not USD.")
    assert rejected.status == "rejected" and rejected.reason == "Not USD." and rejected.decided_by == "ada"

    again = _proposal(engine)

    assert again.id == proposal.id and again.status == "rejected"
    assert engine.proposals.list(user_context=ADMIN) == []
    with pytest.raises(ReviewError) as error:
        engine.proposals.reject(proposal.id, by=ANALYST)
    assert error.value.code == ReviewErrorCode.NOT_AUTHORIZED


def test_new_change_supersedes_the_open_proposal(engine):
    created = []
    engine.events.subscribe(created.append, names=["proposal.created"])
    old = _proposal(engine)
    new = _proposal(engine, change={"description": "Order total, tax included."})

    assert new.id != old.id
    assert engine.proposals.get(old.id, user_context=ADMIN).status == "superseded"
    assert [p.id for p in engine.proposals.list(user_context=ADMIN)] == [new.id]
    assert [e.data["id"] for e in created] == [old.id, new.id]
    # Proposing the same change again changes nothing and publishes nothing.
    _proposal(engine, change={"description": "Order total, tax included."})
    assert len(created) == 2


def test_proposals_list_hides_denied_tables(engine):
    orders = _proposal(engine)
    pay = _proposal(engine, target="salaries.amount", tables=("shop.main.salaries",), change={"role": "money"})

    assert {p.id for p in engine.proposals.list(user_context=ADMIN)} == {orders.id, pay.id}
    assert [p.id for p in engine.proposals.list(user_context=ANALYST)] == [orders.id]
    assert engine.proposals.list(user_context=UserContext(roles=["guest"])) == []
    assert engine.proposals.list(user_context=ADMIN, kind="term") == []
    with pytest.raises(TypeError):
        engine.proposals.list(user_context=None)


def test_get_of_a_hidden_proposal_is_not_found(engine):
    pay = _proposal(engine, target="salaries.amount", tables=("shop.main.salaries",), change={"role": "money"})

    assert engine.proposals.get(pay.id, user_context=ADMIN).id == pay.id
    for caller, wanted in ((ANALYST, pay.id), (ADMIN, "0" * 16)):
        with pytest.raises(ReviewError) as error:
            engine.proposals.get(wanted, user_context=caller)
        assert error.value.code == ReviewErrorCode.NOT_FOUND


# ---------------------------------------------------------------- thumbs-up


def test_thumbs_up_creates_one_proposal_per_change(engine):
    first = _thumbs_up(engine, trace_id="t1")
    assert first.kind == "verified_query" and first.origin == "thumbs_up"
    assert first.tables == ["shop.main.orders"]
    assert first.change["questions"] == ["What is the revenue?"] and first.change["sql"].strip() == SQL
    assert first.change["description"].startswith("TODO")

    _thumbs_up(engine, trace_id="t2")  # the same change from another run: the same row
    assert len(engine.proposals.list(user_context=ADMIN)) == 1

    for skipped in (dict(rating="down"), dict(source="verified"), dict(status="error"),
                    dict(sql="SELECT 1; SELECT 2", datasource="shop")):
        rating = skipped.pop("rating", "up")
        engine.feedback.record(_result(trace_id="t9", **skipped), question="Other?", role="admin", rating=rating)
    assert len(engine.proposals.list(user_context=ADMIN)) == 1

    changed = _thumbs_up(engine, trace_id="t3", sql="SELECT SUM(total) FROM orders WHERE total > 0")
    assert changed.id != first.id
    assert engine.proposals.get(first.id, user_context=ADMIN).status == "superseded"


# ---------------------------------------------------------------- the review file


def test_review_file_round_trip(engine, tmp_path):
    keep = _verified(engine, qid="customers", question="Who are the customers?")
    drop = _proposal(engine)
    out = tmp_path / "review.yaml"

    assert engine.proposals.export(out, user_context=ADMIN) == 2
    document = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert document["version"] == 1
    items = {item["id"]: item for item in document["proposals"]}
    assert set(items) == {keep.id, drop.id} and all(item["decision"] is None for item in items.values())
    items[keep.id]["decision"] = "accept"
    items[keep.id]["change"]["description"] = "Every customer."
    items[drop.id].update(decision="reject", reason="Not USD.")
    out.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = engine.proposals.apply(out, by=ADMIN)

    assert [p.id for p in report.accepted] == [keep.id] and [p.id for p in report.rejected] == [drop.id]
    assert report.rejected[0].reason == "Not USD."
    queries = {q["id"]: q for q in _curated_file(tmp_path)["datasources"]["shop"]["verified_queries"]}
    assert queries["customers"]["description"] == "Every customer."
    # The analyst exports only what they can see, and never over the config's own files.
    _proposal(engine, target="salaries.amount", tables=("shop.main.salaries",), change={"role": "money"})
    assert engine.proposals.export(tmp_path / "mine.yaml", user_context=ANALYST) == 0
    with pytest.raises(ValueError):
        engine.proposals.export(tmp_path / "curated.yaml", user_context=ADMIN)


def test_apply_reports_unknown_ids(engine, tmp_path):
    decided = _proposal(engine)
    engine.proposals.reject(decided.id, by=ADMIN)
    path = tmp_path / "review.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "proposals": [
        {"id": "f" * 16, "decision": "accept", "kind": "column", "datasource_id": "shop", "target": "x",
         "change": {}},
        {"id": decided.id, "decision": "accept", "kind": "column", "datasource_id": "shop",
         "target": "orders.total", "change": decided.change},
    ]}), encoding="utf-8")

    report = engine.proposals.apply(path, by=ADMIN)

    assert report.accepted == [] and set(report.refused) == {"f" * 16, decided.id}
    assert "rejected" in report.refused[decided.id]
    with pytest.raises(ReviewError) as error:
        engine.proposals.apply(path, by=ANALYST)
    assert error.value.code == ReviewErrorCode.NOT_AUTHORIZED


# ---------------------------------------------------------------- retention


def test_decided_proposals_are_purged(engine):
    old = _proposal(engine)
    engine.proposals.reject(old.id, by=ADMIN)
    state = engine._runtime.state
    state.decide_proposal(old.id, "rejected", by="ada", at=datetime.now(timezone.utc) - timedelta(days=400))
    open_ = _proposal(engine, target="orders.id", change={"role": "identifier"})

    counts = engine.storage.purge()

    assert counts["proposals"] == 1
    assert state.get_proposal(old.id) is None and state.get_proposal(open_.id) is not None


# ---------------------------------------------------------------- the CLI


def _cli(folder: Path, *args: str):
    from nl2sql.cli.main import app

    return CliRunner().invoke(app, ["--config", str(folder / "nl2sql.yaml"), *args])


def test_review_commands_list_show_accept_and_reject(tmp_path):
    config = _project(tmp_path)
    with _engine(config) as engine:
        keep, drop = _proposal(engine), _proposal(engine, target="orders.id", change={"role": "identifier"})

    listed = _cli(tmp_path, "review", "list", "--as", "admin")
    assert listed.exit_code == 0, listed.output
    assert keep.id in listed.output and drop.id in listed.output
    shown = _cli(tmp_path, "review", "show", keep.id, "--as", "admin")
    assert shown.exit_code == 0 and "Order total in USD." in shown.output
    as_analyst = _cli(tmp_path, "review", "show", keep.id, "--as", "analyst")
    assert as_analyst.exit_code == 0, as_analyst.output  # orders is visible to the analyst
    refused = _cli(tmp_path, "review", "accept", keep.id, "--as", "analyst")
    assert refused.exit_code == 1 and "admin" in refused.output
    accepted = _cli(tmp_path, "review", "accept", keep.id, "--as", "admin")
    assert accepted.exit_code == 0, accepted.output
    assert "glossary.yaml" in accepted.output
    rejected = _cli(tmp_path, "review", "reject", drop.id, "--as", "admin", "--reason", "Wrong.")
    assert rejected.exit_code == 0, rejected.output
    exported = _cli(tmp_path, "review", "export", "--out", str(tmp_path / "r.yaml"), "--as", "admin")
    assert exported.exit_code == 0 and yaml.safe_load((tmp_path / "r.yaml").read_text())["proposals"] == []


def test_curated_proposals_export_is_gone(tmp_path):
    import nl2sql.feedback as feedback

    _project(tmp_path)
    result = _cli(tmp_path, "curated", "proposals", "export")

    assert result.exit_code != 0
    assert not hasattr(feedback, "build_proposals") and not hasattr(feedback, "write_proposals")
