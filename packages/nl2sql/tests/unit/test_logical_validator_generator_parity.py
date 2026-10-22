"""The validator must reject every plan the generator cannot build.

The validator resolves columns against a *cross-joined* throw-away query
(``_build_validation_query``), which is deliberately blind to join topology.
The generator builds the real FROM clause and walks the join graph, so four
structural mistakes used to pass validation CLEAN and then fail one node later
as a terminal ``SQL_GEN_FAILED`` -- past the last retry edge, with no chance for
the planner to fix them.

Each case here asserts both halves of the invariant: the generator rejects the
plan, and so does the validator, with a retryable code and a message written in
the plan's own vocabulary.
"""
from types import SimpleNamespace

import pytest

from nl2sql.policies import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    JoinSpec,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import (
    SUPPORTED_BINARY_OPS,
    GeneratorNode,
    SqlVisitor,
)
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
from nl2sql_sdk import Limits


def _col(alias: str, name: str) -> Expr:
    return Expr(kind="column", alias=alias, column_name=name)


def _rel(from_table, to_table, from_col, to_col):
    return {
        "from_table": from_table,
        "to_table": to_table,
        "from_columns": [from_col],
        "to_columns": [to_col],
    }


TABLES = [
    Table(
        name="Artist",
        columns=[Column(name="ArtistId", type="int"), Column(name="Name", type="string")],
    ),
    Table(
        name="Album",
        columns=[Column(name="AlbumId", type="int"), Column(name="ArtistId", type="int")],
        relationships=[_rel("Album", "Artist", "ArtistId", "ArtistId")],
    ),
    Table(
        name="Track",
        columns=[Column(name="TrackId", type="int"), Column(name="AlbumId", type="int")],
        relationships=[_rel("Track", "Album", "AlbumId", "AlbumId")],
    ),
]


def _adapter():
    return SimpleNamespace(get_dialect=lambda: "sqlite")


def _ctx():
    registry = stub_ds_registry(_adapter(), limits=Limits(row_limit=1000))
    return fake_runtime(ds_registry=registry, policies={"allow_all": True})


def _state(plan: PlanModel) -> SubgraphExecutionState:
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=TABLES,
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )


def _three_tables():
    return [
        TableRef(name="Artist", alias="t1"),
        TableRef(name="Album", alias="t2"),
        TableRef(name="Track", alias="t3"),
    ]


def _artist_album_join():
    return JoinSpec(
        left_alias="t1",
        right_alias="t2",
        condition=Expr(kind="binary", op="=", left=_col("t1", "ArtistId"), right=_col("t2", "ArtistId")),
    )


def _select_name():
    return [SelectItem(alias="name", expr=_col("t1", "Name"))]


# The four shapes the architecture review measured passing the validator CLEAN
# and failing at generation, plus the operator that crashed the validator.
UNJOINED_ALIAS = PlanModel(
    tables=_three_tables(),
    joins=[_artist_album_join()],
    select_items=_select_name(),
)

NO_JOINS_AT_ALL = PlanModel(
    tables=_three_tables(),
    joins=[],
    select_items=_select_name(),
)

STRANDED_JOIN_ISLAND = PlanModel(
    tables=_three_tables(),
    joins=[
        JoinSpec(
            left_alias="t2",
            right_alias="t3",
            condition=Expr(kind="binary", op="=", left=_col("t2", "AlbumId"), right=_col("t3", "AlbumId")),
        )
    ],
    select_items=_select_name(),
)

SAME_PAIR_JOINED_TWICE = PlanModel(
    tables=_three_tables()[:2],
    joins=[_artist_album_join(), _artist_album_join()],
    select_items=_select_name(),
)

NOT_AS_BINARY_OP = PlanModel(
    tables=[TableRef(name="Artist", alias="t1")],
    joins=[],
    select_items=_select_name(),
    where=Expr(kind="binary", op="NOT", left=_col("t1", "ArtistId"), right=Expr(kind="literal", value=1)),
)


BROKEN_PLANS = {
    "unjoined alias": UNJOINED_ALIAS,
    "no joins at all": NO_JOINS_AT_ALL,
    "stranded join island": STRANDED_JOIN_ISLAND,
    "same pair joined twice": SAME_PAIR_JOINED_TWICE,
    "NOT as a binary operator": NOT_AS_BINARY_OP,
}


def _validator_errors(plan: PlanModel):
    return LogicalValidatorNode(_ctx())(_state(plan))["errors"]


@pytest.mark.parametrize("label", sorted(BROKEN_PLANS))
def test_generator_rejects_the_plan(label):
    # The generator is the baseline: each of these plans must fail there, or
    # the parity assertion below would be vacuous.
    # Arrange
    plan = BROKEN_PLANS[label]

    # Act
    result = GeneratorNode(_ctx())(_state(plan))

    # Assert
    assert [e.error_code for e in result["errors"]] == [ErrorCode.SQL_GEN_FAILED]


@pytest.mark.parametrize("label", sorted(BROKEN_PLANS))
def test_validator_rejects_the_same_plan_retryably(label):
    # Validates the invariant the retry loop depends on: a plan the generator
    # cannot build must be stopped at the validator, which is the only gate
    # with an edge back to the planner.
    # Arrange
    plan = BROKEN_PLANS[label]

    # Act
    errors = _validator_errors(plan)

    # Assert
    blocking = [e for e in errors if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)]
    assert blocking, "the validator passed a plan the generator cannot build"
    assert all(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in blocking)
    assert all(e.is_retryable for e in blocking)


@pytest.mark.parametrize(
    "label, fragments",
    [
        ("unjoined alias", ["t3", "Track", "never joined"]),
        ("no joins at all", ["t2", "t3", "never joined"]),
        ("stranded join island", ["t2-t3", "do not connect"]),
        ("same pair joined twice", ["t1-t2", "already in the query"]),
    ],
)
def test_the_message_names_the_tables_in_plan_vocabulary(label, fragments):
    # Validates the feedback itself: the planner only sees this message, so it
    # must name the plan's own aliases and the tables behind them.
    # Arrange
    plan = BROKEN_PLANS[label]

    # Act
    [error] = [e for e in _validator_errors(plan) if e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE]

    # Assert
    for fragment in fragments:
        assert fragment in error.message, error.message


@pytest.mark.parametrize("label", ["unjoined alias", "no joins at all", "stranded join island"])
def test_the_message_names_the_foreign_keys_that_would_join_them(label):
    # Validates the hint that makes the retry likely to succeed: the schema
    # snapshot knows the foreign keys, so the message spells them out.
    # Arrange
    plan = BROKEN_PLANS[label]

    # Act
    [error] = [e for e in _validator_errors(plan) if e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE]

    # Assert
    assert "t2 (Album).ArtistId = t1 (Artist).ArtistId" in error.message, error.message
    assert "t3 (Track).AlbumId = t2 (Album).AlbumId" in error.message, error.message


def test_not_as_a_binary_operator_is_rejected_cleanly_not_as_a_crash():
    # `op: "NOT"` used to escape the static checks as a ValueError and surface
    # as VALIDATOR_CRASH -- retryable only by accident, and with a message that
    # told the planner nothing it could act on.
    # Arrange / Act
    errors = _validator_errors(NOT_AS_BINARY_OP)

    # Assert
    assert not any(e.error_code == ErrorCode.VALIDATOR_CRASH for e in errors)
    [error] = [e for e in errors if e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE]
    assert "NOT" in error.message


def test_generator_binary_operators():
    # The validator rejects an operator against SUPPORTED_BINARY_OPS rather
    # than by trying to render it, so the set must stay exactly the set the
    # generator's visitor renders -- every op the plan schema admits is either
    # in it or raises.
    # Arrange
    ops = Expr.model_fields["op"].annotation.__args__[0].__args__
    visitor = SqlVisitor()
    one = Expr(kind="literal", value=1)

    for op in ops:
        # Act
        rendered = True
        try:
            visitor.visit(Expr(kind="binary", op=op, left=one, right=one))
        except ValueError:
            rendered = False

        # Assert
        assert rendered == (op in SUPPORTED_BINARY_OPS), op


def test_a_buildable_plan_still_passes():
    # The new check must not reject anything the generator accepts.
    # Arrange
    plan = PlanModel(
        tables=_three_tables()[:2],
        joins=[_artist_album_join()],
        select_items=_select_name(),
    )

    # Act
    errors = _validator_errors(plan)

    # Assert
    assert [e.message for e in errors] == []


def test_existing_error_codes_survive_the_new_check():
    # An unknown table is still TABLE_NOT_FOUND, not swallowed by the
    # buildability check that runs after it.
    # Arrange
    plan = PlanModel(
        tables=[TableRef(name="Nonexistent", alias="t1")],
        joins=[],
        select_items=[SelectItem(alias="name", expr=_col("t1", "Name"))],
    )

    # Act
    errors = _validator_errors(plan)

    # Assert
    assert any(e.error_code == ErrorCode.TABLE_NOT_FOUND for e in errors)


def test_a_registry_without_an_adapter_does_not_break_validation():
    # Unit callers build the validator with a bare namespace as its registry;
    # the dialect lookup must degrade rather than raise.
    # Arrange
    ctx = fake_runtime(
        ds_registry=SimpleNamespace(),
        policies={"allow_all": True},
    )

    # Act
    errors = LogicalValidatorNode(ctx)(_state(UNJOINED_ALIAS))["errors"]

    # Assert
    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in errors)


def test_a_join_key_that_cannot_be_coerced_is_stopped_at_the_validator():
    # D30: the generator builds this plan, and the aggregator could not join a
    # date to a number. The validator is the last gate with a retry edge, so
    # the join-key type check lives there, with a retryable code.
    # Arrange
    from nl2sql.pipeline.nodes.decomposer.schemas import ExpectedColumn

    plan = PlanModel(tables=[TableRef(name="Invoice", alias="t1")],
                     select_items=[SelectItem(alias="customer_id", expr=_col("t1", "InvoiceDate"))])
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q",
                           expected_schema=[ExpectedColumn(name="customer_id", dtype="int", join_key=True)]),
        relevant_tables=[Table(name="Invoice", columns=[Column(name="InvoiceDate", type="datetime")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )

    # Act
    generated = GeneratorNode(_ctx())(state)
    errors = LogicalValidatorNode(_ctx())(state)["errors"]

    # Assert
    assert not generated.get("errors"), generated.get("errors")
    [error] = errors
    assert error.error_code == ErrorCode.JOIN_KEY_TYPE_MISMATCH
    assert error.severity == ErrorSeverity.ERROR and error.is_retryable


# --- a glossary join (docs/architecture/wave_b.md, D46) ----------------------------------------------


def _glossary_join_state(with_glossary: bool) -> SubgraphExecutionState:
    """The relevant tables the schema retriever builds from a snapshot with no declared key, and the plan."""
    from datetime import datetime, timezone

    from nl2sql.knowledge.merge import Knowledge, LoadedSource, merge
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
    from nl2sql_sdk.knowledge import Glossary
    from nl2sql_sdk.schema import (
        ColumnContract, SchemaContract, SchemaMetadata, SchemaSnapshot, TableContract, TableMetadata,
    )
    from nl2sql_sdk.schema import TableRef as SchemaTable

    def table(name, columns):
        ref = SchemaTable(schema_name="main", table_name=name)
        return ref.full_name, TableContract(table=ref, columns={
            c: ColumnContract(name=c, data_type="INTEGER" if c.endswith("Id") else "TEXT", is_primary_key=c == pk)
            for c, pk in columns}), TableMetadata(table=ref)

    tables = [table("Artist", [("ArtistId", "ArtistId"), ("Name", None)]),
              table("Album", [("AlbumId", "AlbumId"), ("ArtistId", None)])]
    snapshot = SchemaSnapshot(
        contract=SchemaContract(datasource_id="ds1", engine_type="sqlite", tables={k: c for k, c, _ in tables}),
        metadata=SchemaMetadata(datasource_id="ds1", engine_type="sqlite", tables={k: m for k, _, m in tables}))
    knowledge = Knowledge()
    if with_glossary:
        glossary = Glossary.model_validate({"version": 1, "datasources": {"ds1": {"joins": [
            {"from": "Album.ArtistId", "to": "Artist.ArtistId", "cardinality": "many-to-one"}]}}})
        knowledge = merge([LoadedSource(type="yaml", glossary=glossary, sha256=None, path=None,
                                        loaded_at=datetime.now(timezone.utc))])
    registry = stub_ds_registry(_adapter(), limits=Limits(row_limit=1000), get_preferred_tables=lambda _id: [],
                                get_dialect=lambda _id: "sqlite")
    ctx = fake_runtime(ds_registry=registry, policies={"allow_all": True}, state=None, knowledge=knowledge,
                       vector_store=SimpleNamespace(snapshot=lambda _id: snapshot))
    state = SubgraphExecutionState(trace_id="t", user_query="q", user_context=UserContext(roles=["admin"]),
                                   sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="albums per artist"))
    retrieved = SchemaRetrieverNode(ctx)(state)
    plan = PlanModel(tables=[TableRef(name="Artist", alias="t1"), TableRef(name="Album", alias="t2")],
                     joins=[_artist_album_join()], select_items=_select_name())
    return state.model_copy(update={"relevant_tables": retrieved["relevant_tables"],
                                    "ast_planner_response": ASTPlannerResponse(plan=plan)})


def test_glossary_join_lets_the_validator_accept_a_plan():
    accepted = LogicalValidatorNode(_ctx())(_glossary_join_state(True)).get("errors") or []
    refused = LogicalValidatorNode(_ctx())(_glossary_join_state(False)).get("errors") or []

    assert not [e for e in accepted if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)]
    assert [e.message for e in refused if e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE][0].startswith(
        "Join does not match any allowed relationship.")


def test_plan_on_a_glossary_join_validates_and_generates():
    state = _glossary_join_state(True)

    result = GeneratorNode(_ctx())(state)

    assert not result.get("errors")
    assert "JOIN" in result["generator_response"].sql_draft.upper()
