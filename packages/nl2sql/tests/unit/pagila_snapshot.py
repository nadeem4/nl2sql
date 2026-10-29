"""A Pagila snapshot for the retriever tests: the sample database's real tables and foreign keys.

Fifteen base tables and the seven views, 22 in all, so a caller that may read
them all is past ``full_snapshot_max_tables`` (15). The columns are Pagila's
own. ``film`` reaches ``payment`` only through ``inventory`` and ``rental``,
three joins, which is why the smoke question needed a join path.
"""
from __future__ import annotations

from typing import Dict, List, Optional

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

DS = "pagila"

# table -> (columns, primary key); a column's type is "text" when it ends in _name or is listed in TEXT.
_TEXT = {"title", "description", "name", "email", "address", "address2", "district", "city", "country", "phone",
         "postal_code", "username", "password", "rating", "film_info", "category", "store", "manager"}
TABLES: Dict[str, tuple] = {
    "actor": (["actor_id", "first_name", "last_name", "last_update"], ["actor_id"]),
    "address": (["address_id", "address", "address2", "district", "city_id", "postal_code", "phone", "last_update"],
                ["address_id"]),
    "category": (["category_id", "name", "last_update"], ["category_id"]),
    "city": (["city_id", "city", "country_id", "last_update"], ["city_id"]),
    "country": (["country_id", "country", "last_update"], ["country_id"]),
    "customer": (["customer_id", "store_id", "first_name", "last_name", "email", "address_id", "active",
                  "create_date", "last_update"], ["customer_id"]),
    "film": (["film_id", "title", "description", "release_year", "language_id", "original_language_id",
              "rental_duration", "rental_rate", "length", "replacement_cost", "rating", "last_update"], ["film_id"]),
    "film_actor": (["actor_id", "film_id", "last_update"], ["actor_id", "film_id"]),
    "film_category": (["film_id", "category_id", "last_update"], ["film_id", "category_id"]),
    "inventory": (["inventory_id", "film_id", "store_id", "last_update"], ["inventory_id"]),
    "language": (["language_id", "name", "last_update"], ["language_id"]),
    "payment": (["payment_id", "customer_id", "staff_id", "rental_id", "amount", "payment_date"], ["payment_id"]),
    "rental": (["rental_id", "rental_date", "inventory_id", "customer_id", "return_date", "staff_id",
                "last_update"], ["rental_id"]),
    "staff": (["staff_id", "first_name", "last_name", "address_id", "email", "store_id", "active", "username",
               "password", "last_update"], ["staff_id"]),
    "store": (["store_id", "manager_staff_id", "address_id", "last_update"], ["store_id"]),
}
VIEWS: Dict[str, List[str]] = {
    "actor_info": ["actor_id", "first_name", "last_name", "film_info"],
    "customer_list": ["id", "name", "address", "zip_code", "phone", "city", "country", "notes", "sid"],
    "film_list": ["fid", "title", "description", "category", "price", "length", "rating", "actors"],
    "nicer_but_slower_film_list": ["fid", "title", "description", "category", "price", "length", "rating", "actors"],
    "sales_by_film_category": ["category", "total_sales"],
    "sales_by_store": ["store", "manager", "total_sales"],
    "staff_list": ["id", "name", "address", "zip_code", "phone", "city", "country", "sid"],
}
# table -> [(column, referred table)]; each refers to the referred table's primary key of the same name,
# except the three renamed ones.
FOREIGN_KEYS: Dict[str, List[tuple]] = {
    "address": [("city_id", "city")], "city": [("country_id", "country")],
    "customer": [("address_id", "address"), ("store_id", "store")],
    "film": [("language_id", "language"), ("original_language_id", "language")],
    "film_actor": [("actor_id", "actor"), ("film_id", "film")],
    "film_category": [("film_id", "film"), ("category_id", "category")],
    "inventory": [("film_id", "film"), ("store_id", "store")],
    "payment": [("customer_id", "customer"), ("rental_id", "rental"), ("staff_id", "staff")],
    "rental": [("customer_id", "customer"), ("inventory_id", "inventory"), ("staff_id", "staff")],
    "staff": [("address_id", "address"), ("store_id", "store")],
    "store": [("address_id", "address"), ("manager_staff_id", "staff")],
}
_REFERRED_COLUMN = {("film", "original_language_id"): "language_id", ("store", "manager_staff_id"): "staff_id"}


def ref(name: str) -> TableRef:
    return TableRef(schema_name="public", table_name=name)


def _column(name: str, pk: bool) -> ColumnContract:
    data_type = "text" if name in _TEXT or name.endswith("_name") else "numeric" if name == "amount" else "int"
    return ColumnContract(name=name, data_type=data_type, is_nullable=not pk, is_primary_key=pk)


def pagila_snapshot(extra_columns: Optional[Dict[str, int]] = None, comments: Optional[Dict[str, str]] = None
                    ) -> SchemaSnapshot:
    """The snapshot. ``extra_columns`` adds that many filler columns to a table; ``comments`` describes tables."""
    extra_columns = extra_columns or {}
    contracts: Dict[str, TableContract] = {}
    for name, (columns, pk) in TABLES.items():
        columns = columns + [f"extra_{i}" for i in range(extra_columns.get(name, 0))]
        contracts[ref(name).full_name] = TableContract(
            table=ref(name), columns={c: _column(c, c in pk) for c in columns},
            foreign_keys=[ForeignKeyContract(
                constrained_columns=[column], referred_table=ref(target),
                referred_columns=[_REFERRED_COLUMN.get((name, column), column)])
                for column, target in FOREIGN_KEYS.get(name, [])])
    for name, columns in VIEWS.items():
        contracts[ref(name).full_name] = TableContract(
            table=ref(name), columns={c: _column(c, False) for c in columns}, foreign_keys=[])
    comments = comments or {}
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id=DS, engine_type="postgresql", tables=contracts),
        metadata=SchemaMetadata(
            datasource_id=DS, engine_type="postgresql",
            tables={key: TableMetadata(table=c.table, row_count=1, columns={},
                                       description=comments.get(c.table.table_name))
                    for key, c in contracts.items()}))
