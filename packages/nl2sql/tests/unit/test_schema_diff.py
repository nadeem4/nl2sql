"""The structural diff between two schema snapshots of one datasource (D1, D31).

docs/architecture/wave_a.md, section 10, "The diff": it compares exactly what
the fingerprint covers, so for two snapshots of one datasource the diff is
empty exactly when the fingerprints are equal. A rename is a drop and an add.
"""
from __future__ import annotations

import random

from nl2sql.schema.diff import diff_contracts, visible_contract, visible_diff
from nl2sql.schema.fingerprint import generate_schema_fingerprint
from nl2sql_sdk.schema import (
    ColumnContract,
    ForeignKeyContract,
    SchemaContract,
    SchemaDiff,
    TableContract,
    TableRef,
)


def _table(schema: str, name: str, *columns: ColumnContract, fks=()) -> TableContract:
    return TableContract(table=TableRef(schema_name=schema, table_name=name),
                         columns={c.name: c for c in columns}, foreign_keys=list(fks))


def _contract(*tables: TableContract, engine_type: str = "sqlite") -> SchemaContract:
    return SchemaContract(datasource_id="shop", engine_type=engine_type,
                          tables={f"{t.table.schema_name}.{t.table.table_name}": t for t in tables})


def _col(name: str, data_type: str = "INTEGER", nullable: bool = True, pk: bool = False) -> ColumnContract:
    return ColumnContract(name=name, data_type=data_type, is_nullable=nullable, is_primary_key=pk)


ORDERS = _table("main", "orders", _col("id", pk=True, nullable=False), _col("customer_id"), _col("total", "REAL"))
CUSTOMERS = _table("main", "customers", _col("id", pk=True, nullable=False), _col("name", "TEXT"))


def _diff(old: SchemaContract, new: SchemaContract) -> SchemaDiff:
    return diff_contracts(old, new, from_version="v1", to_version="v2")


def test_identical_snapshots_have_an_empty_diff():
    diff = _diff(_contract(ORDERS, CUSTOMERS), _contract(ORDERS, CUSTOMERS))

    assert diff.is_empty()
    assert diff.from_version == "v1" and diff.to_version == "v2" and diff.datasource_id == "shop"


def test_added_and_dropped_tables_and_columns():
    new_orders = _table("main", "orders", _col("id", pk=True, nullable=False), _col("customer_id"),
                        _col("placed_at", "TEXT"))
    refunds = _table("main", "refunds", _col("id"))

    diff = _diff(_contract(ORDERS, CUSTOMERS), _contract(new_orders, refunds))

    assert [t.table_name for t in diff.tables_added] == ["refunds"]
    assert [t.table_name for t in diff.tables_dropped] == ["customers"]
    assert [(c.table.table_name, c.column_name) for c in diff.columns_added] == [("orders", "placed_at")]
    assert [(c.table.table_name, c.column_name) for c in diff.columns_dropped] == [("orders", "total")]
    assert diff.counts() == {"tables_added": 1, "tables_dropped": 1, "columns_added": 1, "columns_dropped": 1,
                             "columns_changed": 0, "foreign_keys_added": 0, "foreign_keys_dropped": 0,
                             "engine_type": 0}


def test_column_changes_name_type_nullability_and_primary_key():
    changed = _table("main", "orders", _col("id", "BIGINT", pk=True, nullable=False),
                     _col("customer_id", nullable=False), _col("total", "REAL", pk=True))

    diff = _diff(_contract(ORDERS), _contract(changed))

    by_column = {c.column: c.changes for c in diff.columns_changed}
    assert by_column == {"id": {"type": ("INTEGER", "BIGINT")}, "customer_id": {"nullable": (True, False)},
                         "total": {"pk": (False, True)}}


def test_rename_is_drop_and_add():
    renamed = _table("main", "orders", _col("id", pk=True, nullable=False), _col("client_id"), _col("total", "REAL"))

    diff = _diff(_contract(ORDERS, CUSTOMERS), _contract(renamed, _table("main", "clients", *CUSTOMERS.columns.values())))

    assert [c.column_name for c in diff.columns_dropped] == ["customer_id"]
    assert [c.column_name for c in diff.columns_added] == ["client_id"]
    assert [t.table_name for t in diff.tables_dropped] == ["customers"]
    assert [t.table_name for t in diff.tables_added] == ["clients"]
    assert not diff.columns_changed


def test_engine_type_change_is_in_the_diff():
    diff = _diff(_contract(ORDERS, engine_type="sqlite"), _contract(ORDERS, engine_type="postgres"))

    assert diff.engine_type == ("sqlite", "postgres")
    assert not diff.is_empty()
    assert diff.counts()["engine_type"] == 1


def test_foreign_keys_added_and_dropped():
    fk = ForeignKeyContract(constrained_columns=["customer_id"], referred_table=CUSTOMERS.table,
                            referred_columns=["id"])
    with_fk = _table("main", "orders", *ORDERS.columns.values(), fks=[fk])

    added = _diff(_contract(ORDERS, CUSTOMERS), _contract(with_fk, CUSTOMERS))
    dropped = _diff(_contract(with_fk, CUSTOMERS), _contract(ORDERS, CUSTOMERS))

    [ref] = added.foreign_keys_added
    assert (ref.table.table_name, ref.columns, ref.referred_table.table_name, ref.referred_columns) == \
        ("orders", ["customer_id"], "customers", ["id"])
    assert dropped.foreign_keys_dropped == [ref] and not dropped.foreign_keys_added


def test_diff_round_trips_through_json():
    changed = _table("main", "orders", _col("id", "BIGINT", pk=True, nullable=False), _col("customer_id"))
    diff = _diff(_contract(ORDERS, CUSTOMERS, engine_type="a"), _contract(changed, engine_type="b"))

    assert SchemaDiff.model_validate_json(diff.model_dump_json()) == diff


# -- the property: empty exactly when the fingerprints match -------------------------

_TYPES = ["INTEGER", "TEXT", "REAL"]


def _random_contract(rng: random.Random) -> SchemaContract:
    tables = []
    names = rng.sample(["a", "b", "c", "d"], rng.randint(0, 4))
    for name in names:
        columns = [_col(c, rng.choice(_TYPES), rng.random() < 0.5, rng.random() < 0.3)
                   for c in rng.sample(["x", "y", "z"], rng.randint(1, 3))]
        fks = []
        if names and rng.random() < 0.4:
            target = rng.choice(names)
            fks.append(ForeignKeyContract(constrained_columns=[columns[0].name],
                                          referred_table=TableRef(schema_name=rng.choice(["main", "s"]),
                                                                  table_name=target),
                                          referred_columns=[rng.choice(["x", "y"])]))
        tables.append(_table(rng.choice(["main", "s"]), name, *columns, fks=fks))
    return _contract(*tables, engine_type=rng.choice(["sqlite", "sqlite", "postgres"]))


def _mutate(rng: random.Random, contract: SchemaContract) -> SchemaContract:
    """Either the same contract or one small structural change."""
    if rng.random() < 0.3:
        return contract.model_copy()
    return _random_contract(rng) if rng.random() < 0.5 else contract.model_copy(update={"engine_type": "mssql"})


def test_diff_is_empty_exactly_when_fingerprints_match():
    rng = random.Random(31)
    seen = {True: 0, False: 0}
    for _ in range(600):
        old = _random_contract(rng)
        new = _mutate(rng, old) if rng.random() < 0.6 else _random_contract(rng)
        same = generate_schema_fingerprint(old) == generate_schema_fingerprint(new)
        assert _diff(old, new).is_empty() is same, (old, new)
        seen[same] += 1
    assert seen[True] > 50 and seen[False] > 50


# -- what a caller may see ---------------------------------------------------------------

def test_visible_diff_drops_every_table_the_scope_denies():
    secret = _table("main", "salaries", _col("id"))
    fk = ForeignKeyContract(constrained_columns=["id"], referred_table=secret.table, referred_columns=["id"])
    changed_orders = _table("main", "orders", _col("id", "BIGINT", pk=True, nullable=False), _col("customer_id"),
                            _col("total", "REAL"), _col("bonus"), fks=[fk])
    changed_secret = _table("main", "salaries", _col("id", "TEXT"), _col("amount"))
    diff = _diff(_contract(ORDERS, CUSTOMERS, secret),
                 _contract(changed_orders, changed_secret, _table("main", "payroll", _col("id"))))

    visible = visible_diff(diff, lambda schema, table: table not in {"salaries", "payroll"})

    text = visible.model_dump_json()
    assert "salaries" not in text and "payroll" not in text
    assert [t.table_name for t in visible.tables_dropped] == ["customers"]
    assert [c.column_name for c in visible.columns_added] == ["bonus"]
    assert [c.column for c in visible.columns_changed] == ["id"]
    assert visible.foreign_keys_added == []  # it refers to a denied table
    assert visible.counts()["tables_added"] == 0


def test_visible_contract_drops_denied_tables_and_foreign_keys_to_them():
    secret = _table("main", "salaries", _col("id"))
    fk = ForeignKeyContract(constrained_columns=["id"], referred_table=secret.table, referred_columns=["id"])
    orders = _table("main", "orders", *ORDERS.columns.values(), fks=[fk])

    visible = visible_contract(_contract(orders, secret), lambda schema, table: table != "salaries")

    assert list(visible.tables) == ["main.orders"]
    assert visible.tables["main.orders"].foreign_keys == []
    assert generate_schema_fingerprint(visible) == generate_schema_fingerprint(_contract(ORDERS))
