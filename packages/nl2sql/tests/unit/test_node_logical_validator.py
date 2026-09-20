from types import SimpleNamespace

import pytest

from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    PlanModel,
    TableRef,
    SelectItem,
    Expr,
    JoinSpec,
    ASTPlannerResponse,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery, ExpectedColumn
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.schema_retriever.schema import Table, Column
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.common.settings import settings


def _col(alias: str, name: str):
    return Expr(kind="column", alias=alias, column_name=name)


def _ctx():
    rbac = SimpleNamespace(get_allowed_tables=lambda _ctx: ["*"])
    return SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac)


def test_logical_validator_rejects_missing_plan():
    # Validates plan presence because validation must fail closed.
    # Arrange
    node = LogicalValidatorNode(_ctx())
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        user_context=UserContext(),
    )

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.MISSING_PLAN


def test_logical_validator_detects_join_alias_mismatch():
    # Validates join alias checks because invalid joins must be blocked.
    # Arrange
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[
            JoinSpec(
                left_alias="u",
                right_alias="o",
                join_type="inner",
                ordinal=0,
                condition=Expr(kind="binary", op="=", left=_col("u", "id"), right=_col("o", "user_id")),
            )
        ],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    # Act
    result = node(state)

    # Assert
    assert any(e.error_code == ErrorCode.JOIN_TABLE_NOT_IN_PLAN for e in result["errors"])


def test_logical_validator_expected_schema_mismatch():
    # Validates expected schema enforcement because downstream expects strict columns.
    # Arrange
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), alias="user_id", ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(
            id="sq1",
            datasource_id="ds1",
            intent="q",
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    # Act
    result = node(state)

    # Assert
    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_duplicate_aliases():
    # Validates alias collision detection because duplicate aliases break scoping.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="t", ordinal=0),
            TableRef(name="orders", alias="t", ordinal=1),
        ],
        select_items=[SelectItem(expr=_col("t", "id"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(name="users", columns=[Column(name="id", type="int")]),
            Table(name="orders", columns=[Column(name="id", type="int")]),
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_invalid_ordinals():
    # Validates ordinal checks because non-contiguous ordinals should be rejected.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=1)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=1)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_ambiguous_column_without_alias():
    # Validates ambiguous column detection when alias is omitted.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="u", ordinal=0),
            TableRef(name="orders", alias="o", ordinal=1),
        ],
        select_items=[SelectItem(expr=Expr(kind="column", column_name="id"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(name="users", columns=[Column(name="id", type="int")]),
            Table(name="orders", columns=[Column(name="id", type="int")]),
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.COLUMN_NOT_FOUND for e in result["errors"])


def test_logical_validator_column_not_found_strict_vs_warning(monkeypatch):
    # Validates strict columns toggle because severity depends on settings.
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "missing"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    monkeypatch.setattr(settings, "logical_validator_strict_columns", False)
    node = LogicalValidatorNode(_ctx())
    result = node(state)
    assert any(e.error_code == ErrorCode.COLUMN_NOT_FOUND and e.severity.value == "WARNING" for e in result["errors"])

    monkeypatch.setattr(settings, "logical_validator_strict_columns", True)
    node = LogicalValidatorNode(_ctx())
    result = node(state)
    assert any(e.error_code == ErrorCode.COLUMN_NOT_FOUND and e.severity.value == "ERROR" for e in result["errors"])


def test_logical_validator_rejects_join_not_in_relationships():
    # Validates join relationship enforcement for deterministic planning.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="u", ordinal=0),
            TableRef(name="orders", alias="o", ordinal=1),
        ],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[
            JoinSpec(
                left_alias="u",
                right_alias="o",
                join_type="inner",
                ordinal=0,
                condition=Expr(
                    kind="binary",
                    op="=",
                    left=_col("u", "id"),
                    right=_col("o", "account_id"),
                ),
            )
        ],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(
                name="users",
                columns=[Column(name="id", type="int")],
                relationships=[
                    {
                        "from_table": "users",
                        "to_table": "orders",
                        "from_columns": ["id"],
                        "to_columns": ["user_id"],
                    }
                ],
            ),
            Table(name="orders", columns=[Column(name="user_id", type="int")]),
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_rejects_literal_not_in_stats():
    # Validates literal value enforcement using column stats.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="orders", alias="o", ordinal=0)],
        select_items=[SelectItem(expr=_col("o", "status"), ordinal=0)],
        joins=[],
        where=Expr(
            kind="binary",
            op="=",
            left=_col("o", "status"),
            right=Expr(kind="literal", value="broken"),
        ),
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(
                name="orders",
                columns=[
                    Column(
                        name="status",
                        type="string",
                        stats={"sample_values": ["active", "error", "maintenance"]},
                    )
                ],
            )
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def _album_artist_snapshot():
    """Snapshot shaped exactly like a real one: keys and FK refs are
    ``TableRef.full_name`` (``[schema].[Table]``), not bare table names.

    Mirrors Chinook's ``Album.ArtistId -> Artist.ArtistId`` foreign key.
    """
    from nl2sql_adapter_sdk.schema import (
        ColumnContract,
        ForeignKeyContract,
        SchemaContract,
        SchemaMetadata,
        SchemaSnapshot,
        TableContract,
        TableMetadata,
    )
    from nl2sql_adapter_sdk.schema import TableRef as SdkTableRef

    album = SdkTableRef(schema_name="main", table_name="Album")
    artist = SdkTableRef(schema_name="main", table_name="Artist")

    return SchemaSnapshot(
        contract=SchemaContract(
            datasource_id="chinook",
            engine_type="sqlite",
            tables={
                album.full_name: TableContract(
                    table=album,
                    columns={
                        "AlbumId": ColumnContract(
                            name="AlbumId", data_type="int", is_primary_key=True
                        ),
                        "ArtistId": ColumnContract(name="ArtistId", data_type="int"),
                    },
                    foreign_keys=[
                        ForeignKeyContract(
                            constrained_columns=["ArtistId"],
                            referred_table=artist,
                            referred_columns=["ArtistId"],
                        )
                    ],
                ),
                artist.full_name: TableContract(
                    table=artist,
                    columns={
                        "ArtistId": ColumnContract(
                            name="ArtistId", data_type="int", is_primary_key=True
                        ),
                        "Name": ColumnContract(name="Name", data_type="string"),
                    },
                    foreign_keys=[],
                ),
            },
        ),
        metadata=SchemaMetadata(
            datasource_id="chinook",
            engine_type="sqlite",
            tables={
                album.full_name: TableMetadata(table=album, row_count=347, columns={}),
                artist.full_name: TableMetadata(table=artist, row_count=275, columns={}),
            },
        ),
    )


def _album_artist_plan():
    """The plan for "Which artist has the most albums?" -- a legitimate FK join."""
    return PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="Artist", alias="ar", ordinal=0),
            TableRef(name="Album", alias="al", ordinal=1),
        ],
        select_items=[SelectItem(expr=_col("ar", "Name"), ordinal=0)],
        joins=[
            JoinSpec(
                left_alias="ar",
                right_alias="al",
                join_type="inner",
                ordinal=0,
                condition=Expr(
                    kind="binary",
                    op="=",
                    left=_col("ar", "ArtistId"),
                    right=_col("al", "ArtistId"),
                ),
            )
        ],
    )


def test_logical_validator_accepts_fk_join_from_retriever_output():
    # A genuine FK join must validate against the relationships the schema
    # retriever actually emits, which carry qualified ``[schema].[Table]``
    # names while plan tables carry bare names.
    # Arrange
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode

    snapshot = _album_artist_snapshot()
    retriever = SchemaRetrieverNode(
        SimpleNamespace(
            vector_store=None,
            schema_store=SimpleNamespace(get_latest_snapshot=lambda _id: snapshot),
        )
    )
    relevant_tables = retriever(
        SubgraphExecutionState(
            trace_id="t",
            sub_query=SubQuery(
                id="sq1", datasource_id="chinook", intent="which artist has the most albums"
            ),
        )
    )["relevant_tables"]

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q"),
        relevant_tables=relevant_tables,
        ast_planner_response=ASTPlannerResponse(plan=_album_artist_plan()),
        user_context=UserContext(),
    )

    # Act
    result = LogicalValidatorNode(_ctx())(state)

    # Assert
    assert not [
        e
        for e in result["errors"]
        if e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE
    ], [e.message for e in result["errors"]]


def test_logical_validator_still_rejects_non_fk_join_with_qualified_names():
    # The de-quoting normalization must not make the check permissive: a join
    # on a column pair that is NOT the foreign key is still rejected.
    # Arrange
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode

    snapshot = _album_artist_snapshot()
    retriever = SchemaRetrieverNode(
        SimpleNamespace(
            vector_store=None,
            schema_store=SimpleNamespace(get_latest_snapshot=lambda _id: snapshot),
        )
    )
    relevant_tables = retriever(
        SubgraphExecutionState(
            trace_id="t",
            sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="x"),
        )
    )["relevant_tables"]

    plan = _album_artist_plan()
    # Artist.ArtistId = Album.AlbumId is not the declared foreign key.
    plan.joins[0].condition.right = _col("al", "AlbumId")

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q"),
        relevant_tables=relevant_tables,
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    # Act
    result = LogicalValidatorNode(_ctx())(state)

    # Assert
    assert any(
        e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE
        and e.message == "Join does not match any allowed relationship."
        for e in result["errors"]
    )
