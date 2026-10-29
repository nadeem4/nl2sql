"""PostgreSQL partitions are folded into their parent when the schema is read (wave_c.md, section 18.3, C-W6).

In the real Pagila, ``payment`` is partitioned into ``payment_p2022_01`` ...: the foreign keys are declared on
the partitions, so the parent used to reach the planner with none.
"""
from nl2sql.adapters.postgres.adapter import PostgresAdapter, fold_partitions
from nl2sql_sdk import ForeignKeyContract, TableRef
from nl2sql_sdk.sqlalchemy import ReflectedTable, SQLAlchemyAdapter


def _fk(column: str, target: str) -> ForeignKeyContract:
    return ForeignKeyContract(constrained_columns=[column], referred_table=TableRef(schema_name="public", table_name=target),
                              referred_columns=[column])


def _table(*foreign_keys: ForeignKeyContract) -> ReflectedTable:
    return ReflectedTable(columns=[{"name": "id", "type": "int"}], primary_key=set(), foreign_keys=list(foreign_keys), comment=None)


def _pagila_shape():
    partition_keys = (_fk("customer_id", "customer"), _fk("rental_id", "rental"))
    reflected = {"payment": _table(), "customer": _table(), "rental": _table(_fk("customer_id", "customer"))}
    for month in ("01", "02", "03"):
        reflected[f"payment_p2022_{month}"] = _table(*partition_keys)
    parents = {f"payment_p2022_{month}": "payment" for month in ("01", "02", "03")}
    return reflected, parents


def test_partitions_are_left_out():
    reflected, parents = _pagila_shape()

    folded = fold_partitions(reflected, parents)

    assert sorted(folded) == ["customer", "payment", "rental"]


def test_the_parent_takes_the_partitions_foreign_keys_once():
    reflected, parents = _pagila_shape()

    folded = fold_partitions(reflected, parents)

    keys = [(fk.constrained_columns, fk.referred_table.table_name) for fk in folded["payment"].foreign_keys]
    assert keys == [(["customer_id"], "customer"), (["rental_id"], "rental")]


def test_a_parent_that_declares_its_own_keys_keeps_them():
    reflected, parents = _pagila_shape()
    reflected["payment"] = _table(_fk("staff_id", "staff"))

    folded = fold_partitions(reflected, parents)

    assert [fk.referred_table.table_name for fk in folded["payment"].foreign_keys] == ["staff"]


def test_a_key_that_points_at_a_partition_points_at_its_parent():
    reflected, parents = _pagila_shape()
    reflected["refund"] = _table(_fk("payment_id", "payment_p2022_01"))

    folded = fold_partitions(reflected, parents)

    assert folded["refund"].foreign_keys[0].referred_table.table_name == "payment"


def test_a_sub_partition_folds_into_the_top_parent():
    reflected, parents = _pagila_shape()
    reflected["payment_p2022_01_a"] = _table(_fk("staff_id", "staff"))
    parents["payment_p2022_01_a"] = "payment_p2022_01"

    folded = fold_partitions(reflected, parents)

    assert sorted(folded) == ["customer", "payment", "rental"]
    assert "staff" in [fk.referred_table.table_name for fk in folded["payment"].foreign_keys]


def test_reflect_reads_the_partitions_and_drops_them(monkeypatch):
    reflected, parents = _pagila_shape()
    asked = []

    def base_reflect(self, conn, schema, wants_table, issues):
        asked.append(sorted(n for n in reflected if wants_table(n)))
        return {n: t for n, t in reflected.items() if wants_table(n)}

    monkeypatch.setattr(SQLAlchemyAdapter, "reflect", base_reflect)
    monkeypatch.setattr(PostgresAdapter, "partitions", lambda self, conn, schema: parents)
    adapter = PostgresAdapter.__new__(PostgresAdapter)

    got = adapter.reflect(None, "public", lambda name: name in ("payment", "customer"), [])

    assert sorted(got) == ["customer", "payment"]
    assert "payment_p2022_01" in asked[0]
    assert [fk.referred_table.table_name for fk in got["payment"].foreign_keys] == ["customer", "rental"]
