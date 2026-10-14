"""The package boundaries, enforced by an AST scan of the source tree.

Every rule here is written down in ``docs/architecture/invariants.md`` under
"Package Boundaries Are One-Way" and "Dialect Knowledge Lives in the Adapter",
and stated in short form in the repo's ``CLAUDE.md``. A failure message names
the rule and the file that broke it.

This module is the one home for the boundary rules. Two neighbours stay where
they are because they belong to the package they constrain, and are not
repeated here:

* ``packages/api/tests/test_architecture.py`` -- the REST API imports only the
  top-level ``nl2sql`` namespace, and its response model *is* the engine's
  ``QueryResult``. It runs where ``nl2sql_api`` is installed.
* ``packages/nl2sql/tests/unit/test_logical_validator_security.py`` -- the
  validator's function allow-list, which is the other half of "the model emits
  a plan, never SQL".

The scan is pure ``ast.parse`` over the working tree: no imports of the code
under test, and well under a second for the whole monorepo.
"""
from __future__ import annotations

import ast
import functools
import pathlib
import re
import subprocess
import sys
from importlib.metadata import entry_points

import pytest

RULES = "docs/architecture/invariants.md"

PACKAGES = pathlib.Path(__file__).resolve().parents[3]
ENGINE = PACKAGES / "nl2sql" / "src" / "nl2sql"
SDK = PACKAGES / "sdk" / "src" / "nl2sql_sdk"
ADAPTERS = ENGINE / "adapters"
SOURCES = [ENGINE, SDK, PACKAGES / "api" / "src" / "nl2sql_api"]
REPO = PACKAGES.parent
SCRIPTS = REPO / "scripts"


# --------------------------------------------------------------------------
# The scan
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _tree(path: pathlib.Path) -> ast.Module:
    """The parsed file. Cached: every rule below walks the same few hundred files."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_name(path: pathlib.Path, root: pathlib.Path) -> str:
    parts = [root.name, *path.relative_to(root).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@functools.lru_cache(maxsize=None)
def _lazy_nodes(tree: ast.Module) -> frozenset[int]:
    """The ids of every node inside a function body: a deferred import."""
    lazy: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lazy.update(id(child) for child in ast.walk(node))
    return frozenset(lazy)


def _root_of(path: pathlib.Path) -> pathlib.Path:
    return next(root for root in SOURCES if path.is_relative_to(root))


def imports(path: pathlib.Path):
    """Yield ``(lineno, imported module, at module scope)`` for one file.

    Relative imports are resolved against the file's own package, so
    ``from .adapter import X`` in ``adapters/sqlite/`` reads as
    ``nl2sql.adapters.sqlite.adapter``.
    """
    tree = _tree(path)
    lazy = _lazy_nodes(tree)
    package = _module_name(path, _root_of(path))
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    for node in ast.walk(tree):
        at_module_scope = id(node) not in lazy
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, at_module_scope
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
            yield node.lineno, base, at_module_scope
            for alias in node.names:
                yield node.lineno, f"{base}.{alias.name}", at_module_scope


@functools.lru_cache(maxsize=None)
def _docstrings(tree: ast.Module) -> frozenset[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return frozenset(out)


def literals(path: pathlib.Path):
    """Yield ``(lineno, text)`` for every string literal that is not a docstring.

    f-string chunks are included: the date bug this rule exists for was an
    f-string. Comments are not: a comment cannot reach a database.
    """
    tree = _tree(path)
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


@functools.lru_cache(maxsize=None)
def python_files(*roots: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return tuple(path for root in roots for path in sorted(root.rglob("*.py")))


def _where(path: pathlib.Path) -> str:
    """``path`` relative to ``packages/``, or to the repository for a file outside it (``scripts/``)."""
    root = PACKAGES if PACKAGES in path.parents else REPO
    return str(path.relative_to(root)).replace("\\", "/")


def _broke(rule: str, offenders: list[str]) -> str:
    return f"{rule}\nThe rule is written down in {RULES}.\nBroken by:\n  " + "\n  ".join(offenders)


# --------------------------------------------------------------------------
# 1. The SDK depends on nothing
# --------------------------------------------------------------------------

SDK_SQLALCHEMY = SDK / "sqlalchemy"


def test_sdk_core_imports_only_pydantic_and_the_stdlib():
    """An adapter author installs the SDK, not the engine. ``nl2sql_sdk.sqlalchemy`` is the one extra."""
    allowed = {"pydantic", "nl2sql_sdk"} | set(sys.stdlib_module_names)
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(SDK)
        if not path.is_relative_to(SDK_SQLALCHEMY)
        for lineno, name, _ in imports(path)
        if name.partition(".")[0] not in allowed
        and not name.startswith("nl2sql_sdk.sqlalchemy")
    ]
    assert not offenders, _broke(
        "The SDK imports only pydantic and the standard library: it is the "
        "contract a third-party adapter compiles against, so it must not drag in the "
        "engine, SQLAlchemy or a driver. The SQLAlchemy base is nl2sql_sdk.sqlalchemy, "
        "installed with nl2sql-sdk[sqlalchemy], and the core never imports it.", offenders)


def test_the_sdk_sqlalchemy_base_imports_only_sqlalchemy_sqlglot_pydantic_and_the_stdlib():
    """``nl2sql_sdk.sqlalchemy`` is what an adapter on SQLAlchemy builds on: no engine, no driver."""
    allowed = {"pydantic", "nl2sql_sdk", "sqlalchemy", "sqlglot"} | set(sys.stdlib_module_names)
    assert SDK_SQLALCHEMY.is_dir(), "nl2sql_sdk/sqlalchemy/ holds the public SQLAlchemy base."
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(SDK_SQLALCHEMY)
        for lineno, name, _ in imports(path)
        if name.partition(".")[0] not in allowed
    ]
    assert not offenders, _broke(
        "nl2sql_sdk.sqlalchemy imports only SQLAlchemy, sqlglot, pydantic and the standard "
        "library: an adapter author installs nl2sql-sdk[sqlalchemy], not the engine.", offenders)


def test_the_engine_has_no_sqlalchemy_base_of_its_own():
    """The built-ins build on ``nl2sql_sdk.sqlalchemy``, the base any adapter author uses."""
    assert not (ADAPTERS / "sqlalchemy_base").exists(), (
        "nl2sql/adapters/sqlalchemy_base/ moved to nl2sql_sdk/sqlalchemy/ (adapter SDK v2).")


def test_the_sdk_stores_module_imports_only_pydantic():
    """A store plugin (Qdrant, S3) installs the SDK for ``nl2sql_sdk.stores``, not the engine."""
    path = SDK / "stores.py"
    allowed = {"pydantic", "nl2sql_sdk"} | set(sys.stdlib_module_names)
    assert path.is_file(), "nl2sql_sdk/stores.py holds the four store interfaces."
    offenders = [f"{_where(path)}:{lineno} {name}" for lineno, name, _ in imports(path)
                 if name.partition(".")[0] not in allowed]
    assert not offenders, _broke(
        "nl2sql_sdk.stores imports only pydantic and the standard library: a store "
        "plugin compiles against it without the engine.", offenders)


def test_no_file_imports_the_sdk_by_its_old_name():
    """The SDK is ``nl2sql_sdk``; ``nl2sql_adapter_sdk`` is never published again."""
    roots = [PACKAGES, PACKAGES.parent / "scripts"]
    offenders = [
        f"{path}:{lineno} {name}"
        for path in python_files(*roots)
        if "node_modules" not in path.parts
        for lineno, name, _ in _absolute_imports(path)
        if name.partition(".")[0] == "nl2sql_adapter_sdk"
    ]
    assert not offenders, _broke(
        "The SDK package is nl2sql_sdk. Import it by that name.", offenders)


def _absolute_imports(path: pathlib.Path):
    """``(lineno, module)`` for every absolute import in any file, inside or outside SOURCES."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, True
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            yield node.lineno, node.module, True


def test_no_module_imports_nl2sql_configs():
    """0.5 reads one config, ``nl2sql.config``; the 0.4 file envelopes in ``nl2sql.configs`` are gone."""
    roots = [PACKAGES, PACKAGES.parent / "scripts"]
    offenders = [
        f"{path}:{lineno} {name}"
        for path in python_files(*roots)
        if "node_modules" not in path.parts
        for lineno, name, _ in _absolute_imports(path)
        if name in {"nl2sql.configs", "nl2sql._legacy"} or name.startswith(("nl2sql.configs.", "nl2sql._legacy."))
    ]
    assert not offenders, _broke(
        "Nothing imports nl2sql.configs or nl2sql._legacy. The config is nl2sql.config "
        "(Config, load_config); the curated checks are in nl2sql.config.curated.", offenders)
    assert not (ENGINE / "configs").exists(), "nl2sql/configs/ is deleted in 0.5."


# --------------------------------------------------------------------------
# 2-4. The adapters are a leaf, and only they touch a database library
# --------------------------------------------------------------------------

# SQLAlchemy is the shared connection and reflection layer every bundled
# adapter is built on; sqlglot is the expression vocabulary the engine hands
# the adapter to render. Both are adapter-layer libraries, not engine ones.
ADAPTER_LIBRARIES = {"nl2sql_sdk", "pydantic", "sqlalchemy", "sqlglot", "nl2sql.adapters"}

# The database libraries only an adapter may import.
DATABASE_LIBRARIES = {"sqlalchemy", "psycopg", "psycopg2", "pymysql", "MySQLdb", "pyodbc", "duckdb",
                      "snowflake", "cx_Oracle", "oracledb", "asyncpg", "aiosqlite"}

# The engine's own in-process compute engine: DuckDB combines sub-query results
# that have already been fetched. It is not a datasource.
DATABASE_LIBRARY_EXCEPTIONS = {"nl2sql/src/nl2sql/aggregation/engines/polars_duckdb.py": {"duckdb"}}


def test_the_adapters_import_only_the_sdk_their_driver_and_the_shared_sql_layer():
    """No adapter may reach back into the engine, or the layering is a circle."""
    offenders = []
    for path in python_files(ADAPTERS):
        for lineno, name, _ in imports(path):
            top = name.partition(".")[0]
            if top in sys.stdlib_module_names or name.startswith("nl2sql.adapters"):
                continue
            if top in DATABASE_LIBRARIES or any(name == lib or name.startswith(lib + ".")
                                                for lib in ADAPTER_LIBRARIES):
                continue
            offenders.append(f"{_where(path)}:{lineno} {name}")
    assert not offenders, _broke(
        "An adapter imports only the SDK, its driver, SQLAlchemy, sqlglot, pydantic and "
        "the standard library. The engine reaches adapters through the "
        "'nl2sql.adapters' entry points, never the other way round.", offenders)


def test_nothing_outside_the_adapters_imports_an_adapter():
    """Adapters are discovered, never named: that is what makes them pluggable."""
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(*SOURCES)
        if not path.is_relative_to(ADAPTERS)
        for lineno, name, _ in imports(path)
        if name == "nl2sql.adapters" or name.startswith("nl2sql.adapters.")
    ]
    assert not offenders, _broke(
        "Nothing outside nl2sql/adapters/ imports an adapter. Adapters are loaded through "
        "the 'nl2sql.adapters' entry points and used through DatasourceAdapter, so "
        "a datasource the repo has never heard of works the same way the bundled ones do.",
        offenders)


def test_nothing_outside_the_adapters_imports_a_database_library():
    """SQLAlchemy in the pipeline would be a second, unpluggable way to reach a database."""
    offenders = []
    for path in python_files(*SOURCES):
        if path.is_relative_to(ADAPTERS) or path.is_relative_to(SDK_SQLALCHEMY):
            continue
        allowed = DATABASE_LIBRARY_EXCEPTIONS.get(_where(path), set())
        offenders += [f"{_where(path)}:{lineno} {name}"
                      for lineno, name, _ in imports(path)
                      if name.partition(".")[0] in DATABASE_LIBRARIES - allowed]
    assert not offenders, _broke(
        "Only nl2sql/adapters/ (and the SDK's SQLAlchemy base, nl2sql_sdk/sqlalchemy/) "
        "imports SQLAlchemy or a database driver. sqlite3 is not on "
        "this list: it is the standard library behind the engine's own schema and feedback "
        "stores, which are metadata, not a datasource.", offenders)


STORAGE = ENGINE / "storage"

# The vector library and its LangChain wrapper. The local embedder is the one
# exception: its model is the ONNX all-MiniLM-L6-v2 that ships inside chromadb,
# so it borrows chromadb's embedding function, not its index.
INDEX_LIBRARIES = {"chromadb", "langchain_chroma"}
INDEX_LIBRARY_EXCEPTIONS = {"nl2sql/src/nl2sql/embeddings/local.py": {"chromadb"}}


def test_only_storage_imports_chromadb():
    """The engine reaches its index through ``IndexStore``; Chroma is one store among any."""
    offenders = []
    for path in python_files(*SOURCES):
        if path.is_relative_to(STORAGE):
            continue
        allowed = INDEX_LIBRARY_EXCEPTIONS.get(_where(path), set())
        offenders += [f"{_where(path)}:{lineno} {name}"
                      for lineno, name, _ in imports(path)
                      if name.partition(".")[0] in INDEX_LIBRARIES - allowed]
    assert not offenders, _broke(
        "Only nl2sql/storage/ imports chromadb. The engine embeds, ranks and filters through "
        "the IndexStore interface (nl2sql_sdk.stores), so a plugin index store works the same "
        "way as the built-in one.", offenders)


# The SQLite adapter's driver is sqlite3, and the gold set's SQL runs on the
# demo's Chinook file to score a benchmark: both read a datasource, not the
# engine's own state.
STATE_LIBRARY_EXCEPTIONS = {"nl2sql/src/nl2sql/evaluation/gold.py"}


def test_only_storage_imports_sqlite3():
    """The engine keeps its state through ``StateStore``; SQLite is one state store among any."""
    offenders = []
    for path in python_files(*SOURCES):
        if path.is_relative_to(STORAGE) or path.is_relative_to(ADAPTERS) or _where(path) in STATE_LIBRARY_EXCEPTIONS:
            continue
        offenders += [f"{_where(path)}:{lineno} {name}"
                      for lineno, name, _ in imports(path)
                      if name.partition(".")[0] == "sqlite3"]
    assert not offenders, _broke(
        "Only nl2sql/storage/ imports sqlite3. Snapshots, the plan cache, feedback and the lock "
        "are reached through the StateStore interface (nl2sql_sdk.stores), so a plugin state "
        "store works the same way as the built-in one.", offenders)


# --------------------------------------------------------------------------
# 5-6. Dialect knowledge lives in the adapter
# --------------------------------------------------------------------------

# Where a dialect name or a dialect's SQL would be a boundary break. The CLI is
# not here: it names adapters to install and configure them, which is registry
# knowledge, not SQL. Neither is datasets/, whose literals are file names.
DIALECT_SCAN = ["pipeline", "execution", "indexing", "evaluation", "api", "llm", "aggregation",
                "schema", "services", "datasources", "policies", "tracing", "storage", "runtime.py",
                "public_api.py"]

DIALECT_NAMES = re.compile(r"(?i)\b(sqlite|postgres|postgresql|mysql|mssql|tsql|duckdb|snowflake|bigquery)\b")

# Functions one database has and the next spells differently. The engine plans
# with portable operations (DATE_PART, DATE_TRUNC); rendering these is the
# adapter's job, through sqlglot or render_sql().
DIALECT_ONLY_SQL = re.compile(
    r"(?i)\b(STRFTIME|JULIANDAY|GETDATE|SYSDATE|DATEADD|DATEDIFF|DATEPART|DATE_FORMAT|TO_CHAR"
    r"|ILIKE|NVL|IIF|GROUP_CONCAT|STRING_AGG|LISTAGG)\b")

# Two things that carry a database's name without being a datasource: the
# engine's own state store, a SQLite file by default (the built-in ``sqlite``
# store), and the in-process polars/DuckDB compute engine that combines
# sub-query results already fetched through an adapter.
DIALECT_NAME_EXCEPTIONS = {
    "nl2sql/src/nl2sql/storage/sqlite.py",
    "nl2sql/src/nl2sql/aggregation/engines/polars_duckdb.py",
}

# The plan's one function vocabulary: the allow-list the validator enforces, and
# the only place in the engine where a function name may be written down.
# STRFTIME is on it because a SQLite plan may still call it by name; every other
# module -- prompt, generator, validator message -- must say DATE_PART instead.
DIALECT_SQL_EXCEPTIONS = {"nl2sql/src/nl2sql/pipeline/nodes/ast_planner/functions.py"}


def _dialect_scan_files():
    for name in DIALECT_SCAN:
        target = ENGINE / name
        yield from (python_files(target) if target.is_dir() else [target])


def test_no_dialect_name_outside_the_adapters():
    offenders = [
        f"{_where(path)}:{lineno} {match.group(0)!r} in {text[:60]!r}"
        for path in _dialect_scan_files()
        if _where(path) not in DIALECT_NAME_EXCEPTIONS
        for lineno, text in literals(path)
        if (match := DIALECT_NAMES.search(text))
    ]
    assert not offenders, _broke(
        "No engine module names a database dialect. The adapter declares its dialect "
        "through get_dialect(); the engine passes that name to sqlglot and never "
        "branches on it. A branch on the name is a switch that every new adapter has "
        "to be added to.", offenders)


def test_no_dialect_specific_sql_outside_the_adapters():
    offenders = [
        f"{_where(path)}:{lineno} {match.group(0)!r} in {text[:60]!r}"
        for path in _dialect_scan_files()
        if _where(path) not in DIALECT_SQL_EXCEPTIONS
        for lineno, text in literals(path)
        if (match := DIALECT_ONLY_SQL.search(text))
    ]
    assert not offenders, _broke(
        "No engine module writes one database's SQL -- not in a prompt, not in an error "
        "message, not in generated SQL. The plan says what ('the year of this date'); "
        "the adapter says how (STRFTIME on SQLite, EXTRACT on Postgres). SQL that only "
        "one database understands, written in the engine, is wrong for every other.",
        offenders)


# --------------------------------------------------------------------------
# 7. get_dialect() is a name sqlglot knows (moved from
#    tests/adapters/unit/test_adapter_dialect_names.py)
# --------------------------------------------------------------------------

# One per name: an editable install next to a checkout can list a name twice.
ENTRY_POINTS = sorted({ep.name: ep for ep in entry_points(group="nl2sql.adapters")}.values(),
                      key=lambda ep: ep.name)


def test_every_bundled_adapter_is_checked():
    assert {ep.name for ep in ENTRY_POINTS} >= {"duckdb", "mssql", "mysql", "postgres", "sqlite"}


@pytest.mark.parametrize("entry_point", ENTRY_POINTS, ids=lambda ep: ep.name)
def test_get_dialect_is_a_sqlglot_dialect(entry_point):
    """The generator renders with ``query.sql(dialect=adapter.get_dialect())``.

    Postgres and SQL Server used to return SQLAlchemy's names (``postgresql``,
    ``mssql``), which sqlglot rejects, so no SQL could be generated for either.
    """
    from sqlglot.dialects.dialect import Dialect

    cls = entry_point.load()
    adapter = cls.__new__(cls)  # no connection: the dialect is a constant

    try:
        Dialect.get_or_raise(adapter.get_dialect())
    except Exception as exc:  # noqa: BLE001 -- the message is the point
        pytest.fail(_broke(f"{entry_point.name}: get_dialect() must return a name sqlglot accepts, "
                           f"not SQLAlchemy's. {exc}", [entry_point.value]))


@pytest.mark.parametrize("name, dialect", [
    ("postgres", "postgres"), ("mssql", "tsql"), ("mysql", "mysql"), ("duckdb", "duckdb"), ("sqlite", "sqlite"),
])
def test_bundled_adapter_dialects(name, dialect):
    cls = next(ep for ep in ENTRY_POINTS if ep.name == name).load()

    assert cls.__new__(cls).get_dialect() == dialect


# --------------------------------------------------------------------------
# 8. The CLI is a client, not a library (moved from
#    tests/unit/test_cli_demo_boundaries.py)
# --------------------------------------------------------------------------

CLI = ENGINE / "cli"
DEMO = CLI / "demo"


def test_nothing_outside_the_cli_imports_the_cli():
    """The engine is a library first: the CLI, the API and the playground are its callers."""
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(*SOURCES)
        if not path.is_relative_to(CLI)
        for lineno, name, _ in imports(path)
        if name == "nl2sql.cli" or name.startswith("nl2sql.cli.")
    ]
    assert not offenders, _broke(
        "Nothing outside nl2sql/cli/ imports the CLI. Knowledge an SDK or REST caller also "
        "needs -- providers, models, key variables -- belongs in the engine (nl2sql/llm/), "
        "with the CLI reading it from there.", offenders)


def test_cli_demo_never_imports_cli_commands():
    """A command is an entry point. Importing one makes its argument parsing a dependency."""
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(DEMO)
        for lineno, name, _ in imports(path)
        if name == "nl2sql.cli.commands" or name.startswith("nl2sql.cli.commands.")
    ]
    assert not offenders, _broke(
        "nl2sql.cli.demo never imports nl2sql.cli.commands: a helper the demo and a command "
        "share lives in cli/demo/ or cli/common/.", offenders)


def test_the_cli_the_playground_and_the_api_use_only_the_public_api():
    """The CLI, the playground and nl2sql-api are clients of the facade, like any SDK caller.

    None of them reaches the runtime behind it (``engine._runtime``, the
    removed ``engine.context``, a namespace's ``_ctx``): what one of them needs
    is a facade method every caller can use (owner decision, PR 10).
    """
    hidden = {"context", "_ctx", "_runtime"}
    offenders = []
    for path in python_files(ENGINE / "cli", PACKAGES / "api" / "src" / "nl2sql_api"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Attribute) and node.attr in hidden:
                offenders.append(f"{_where(path)}:{node.lineno} .{node.attr}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                  and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                  and node.args[1].value in hidden):
                offenders.append(f"{_where(path)}:{node.lineno} getattr(..., {node.args[1].value!r})")
    assert not offenders, _broke(
        "The CLI, the playground and nl2sql-api reach the engine only through NL2SQL's public "
        "methods and namespaces, never engine._runtime. Anything they need from the runtime is "
        "a facade method every caller can use.", offenders)


# What the playground and ``nl2sql demo`` still import from inside the engine,
# and why. Everything else comes from the top-level ``nl2sql``. The list only
# shrinks: nl2sql-api gets none of it.
STILL_IMPORTED_FROM_INSIDE = {
    "nl2sql.llm.providers": "provider knowledge the CLI reads from the engine: labels, verified models, "
                            "the key shape (CLAUDE.md)",
    "nl2sql.llm.request_key": "the hosted demo's per-request keys, until Phase 4 moves the web app onto "
                              "nl2sql-api",
    "nl2sql.llm.replay": "nl2sql demo's recording proxy and replay store",
    "nl2sql.testing.fake_llm": "nl2sql demo's keyless replay server",
    "nl2sql.datasets.demo": "nl2sql demo's guided questions",
    "nl2sql.config": "nl2sql demo writes its project with init_project, as nl2sql init does",
}


def _engine_imports(path: pathlib.Path):
    """``(lineno, module)`` for every import of an ``nl2sql`` module, at any scope."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        yield from ((node.lineno, name) for name in names if name == "nl2sql" or name.startswith("nl2sql."))


def test_the_playground_the_demo_command_and_the_api_import_only_the_public_nl2sql():
    """They are clients of the facade: ``from nl2sql import ...``, never the engine's internals.

    Feedback, traces, the pipeline's steps, the index's defaults and the
    provider list are facade methods (``engine.feedback``, ``engine.traces``,
    ``engine.about()``, ``engine.index``, ``engine.llm.providers()``), so
    ``nl2sql.feedback``, ``nl2sql.tracing``, ``nl2sql.pipeline``,
    ``nl2sql.indexing`` and ``PROVIDER_PRESETS`` are out of reach.
    """
    api = PACKAGES / "api" / "src" / "nl2sql_api"
    offenders = []
    for path in python_files(DEMO / "playground", api) + (CLI / "commands" / "demo.py",):
        in_api = path.is_relative_to(api)
        for lineno, name in _engine_imports(path):
            allowed = name == "nl2sql" or (not in_api and (
                name.startswith("nl2sql.cli.") or name in STILL_IMPORTED_FROM_INSIDE))
            if not allowed:
                offenders.append(f"{_where(path)}:{lineno} {name}")
    assert not offenders, _broke(
        "The playground, nl2sql demo and nl2sql-api import only the top-level nl2sql (plus, for "
        "the first two, their own CLI package and STILL_IMPORTED_FROM_INSIDE). What they need from "
        "the engine is a facade method every caller can use.", offenders)


# --------------------------------------------------------------------------
# 9. The runtime does not pay for the benchmark (moved from
#    tests/unit/test_import_hygiene.py)
# --------------------------------------------------------------------------

RUNTIME = ["pipeline", "execution", "indexing", "datasources", "llm", "policies", "aggregation", "schema",
           "services", "runtime.py", "public_api.py"]


def test_the_runtime_never_imports_evaluation_or_feedback_at_module_scope():
    """Answering a question must not load the benchmark or the feedback store."""
    offenders = []
    for name in RUNTIME:
        target = ENGINE / name
        for path in python_files(target) if target.is_dir() else [target]:
            offenders += [f"{_where(path)}:{lineno} {imported}"
                          for lineno, imported, at_module_scope in imports(path)
                          if at_module_scope and re.match(r"nl2sql\.(evaluation|feedback)\b", imported)]
    assert not offenders, _broke(
        "The runtime never imports nl2sql.evaluation or nl2sql.feedback at module scope. "
        "Both are opt-in tools; importing one costs every SDK user the YAML loaders, the "
        "gold dataset and the fake LLM. A deferred import inside a function is fine.",
        offenders)


def _run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_import_nl2sql_does_not_load_evaluation():
    assert _run("import sys, nl2sql; print('nl2sql.evaluation' in sys.modules)") == "False", \
        f"import nl2sql loaded nl2sql.evaluation. The rule is written down in {RULES}."


FORBIDDEN_AT_IMPORT = ("chromadb", "onnxruntime", "sqlalchemy", "psycopg2", "psycopg", "pymysql",
                       "pyodbc", "pymssql", "duckdb", "duckdb_engine", "textual", "opentelemetry")

IMPORT_BUDGET_US = 500_000


@pytest.mark.parametrize("statement", ["import nl2sql", "from nl2sql import load_config, Registry; Registry.default()",
                                       "from nl2sql import Config, Datasource, CheckReport, EngineNotReady"])
def test_importing_nl2sql_loads_no_heavy_library(statement):
    loaded = _run(f"import sys; {statement}; "
                  f"print(sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_AT_IMPORT!r})))")
    assert loaded == "[]", (
        f"{statement} loaded {loaded}. Import heavy libraries where they are used, "
        f"not at module scope on the import path of nl2sql. The rule is in {RULES}.")


def _import_us(module: str) -> int:
    """The cumulative microseconds ``python -X importtime`` reports for ``module``."""
    out = subprocess.run([sys.executable, "-X", "importtime", "-c", f"import {module}"],
                         capture_output=True, text=True, check=True)
    for line in out.stderr.splitlines():
        # "import time: <self us> | <cumulative us> | <module>"
        fields = line.split("|")
        if len(fields) == 3 and fields[2].strip() == module:
            return int(fields[1].strip())
    raise AssertionError(f"-X importtime reported no line for {module}:\n{out.stderr[-2000:]}")


def test_import_nl2sql_stays_within_its_time_budget():
    # Imports only, not wall-clock time, and the best of five, so a busy CI
    # runner that slows one run down does not fail the build.
    best = min(_import_us("nl2sql") for _ in range(5))
    assert best < IMPORT_BUDGET_US, (
        f"import nl2sql took {best / 1000:.0f} ms of imports, over the "
        f"{IMPORT_BUDGET_US / 1000:.0f} ms budget. Something on the import path of nl2sql "
        f"pulls in the runtime at module scope. The rule is in {RULES}.")


def test_building_the_benchmark_api_loads_evaluation_on_demand():
    code = ("import sys, nl2sql; from nl2sql import BenchmarkAPI, BenchmarkConfig; "
            "print('nl2sql.evaluation' in sys.modules, BenchmarkConfig.__module__)")
    assert _run(code) == "True nl2sql.evaluation.types"


def test_the_engine_builds_its_benchmark_api_lazily():
    from nl2sql.public_api import NL2SQL

    assert isinstance(NL2SQL.__dict__["benchmark"], property)


def test_the_plan_cache_imports_without_the_nodes_package_importing_it_back():
    """``plan_cache`` needs ``PlanModel``; ``ast_planner.node`` needs ``PlanCache``.

    While ``nodes/__init__`` and ``nodes/ast_planner/__init__`` re-exported the
    node classes, importing the schemas ran the node first, and the pair was a
    cycle that only stayed hidden because some earlier import happened to load
    them in the lucky order. Importing the cache on its own is the test.
    """
    assert _run("import nl2sql.pipeline.plan_cache; print('ok')") == "ok",         f"nl2sql.pipeline.plan_cache cannot be imported on its own. The rule is written down in {RULES}."


def test_aggregation_never_imports_the_pipeline():
    """The pipeline's aggregator node calls the aggregation service; the reverse was a cycle."""
    offenders = [f"{_where(path)}:{lineno} {name}"
                 for path in python_files(ENGINE / "aggregation")
                 for lineno, name, _ in imports(path)
                 if name.startswith("nl2sql.pipeline")]
    assert not offenders, _broke(
        "nl2sql.aggregation never imports nl2sql.pipeline. The DAG models both need live "
        "in nl2sql.execution.dag.", offenders)


# --------------------------------------------------------------------------
# 9b. Every access decision goes through a Scope
# --------------------------------------------------------------------------

POLICIES = ENGINE / "policies"
# The places that decide table access. Each asks the caller's Scope.
ACCESS_DECISIONS = [
    "pipeline/nodes/datasource_resolver/node.py",
    "pipeline/nodes/curated_matcher/node.py",
    "pipeline/nodes/schema_retriever/node.py",
    "pipeline/nodes/validator/node.py",
    "pipeline/nodes/executor/node.py",
    "api/policy_api.py",
]
# The 0.5 access helpers. A second way to decide is how two nodes come to disagree.
OLD_ACCESS_NAMES = {"table_allowed", "get_allowed_tables", "get_allowed_datasources", "RBAC", "RolePolicy"}


def test_every_access_decision_goes_through_scope():
    engine_files = [p for p in python_files(ENGINE) if POLICIES not in p.parents and ADAPTERS not in p.parents]
    offenders = []
    for path in engine_files:
        for lineno, imported, _ in imports(path):
            # Pattern matching is the policies package's alone; the config only
            # asks it whether a pattern is well formed.
            if imported == "fnmatch" or (imported.startswith("nl2sql.policies.patterns")
                                         and _where(path) != "nl2sql/src/nl2sql/config/models.py"):
                offenders.append(f"{_where(path)}:{lineno} imports {imported}")
            if imported.startswith("nl2sql.auth"):
                offenders.append(f"{_where(path)}:{lineno} imports {imported}")
        for node in ast.walk(_tree(path)):
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if name in OLD_ACCESS_NAMES:
                offenders.append(f"{_where(path)}:{node.lineno} uses {name}")
    for relative in ACCESS_DECISIONS:
        path = ENGINE / relative
        calls = [n for n in ast.walk(_tree(path))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "scope"]
        if not calls:
            offenders.append(f"{_where(path)} decides access without asking a Scope")
    assert not offenders, _broke(
        "Every place that decides table access asks the caller's Scope "
        "(PolicyEngine.scope), and nothing outside nl2sql/policies/ matches policy patterns. "
        "One decision, made one way, is what keeps the planner, the validator and the "
        "executor from disagreeing about a table.", offenders)


# --------------------------------------------------------------------------
# 10. Provider knowledge has one home
# --------------------------------------------------------------------------

PROVIDER_KEY_VARIABLE = re.compile(r"^[A-Z0-9]+_API_KEY$")

def _environment_reads(path: pathlib.Path):
    """Yield ``(lineno, name)`` for every read of ``os.environ``/``os.getenv``."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) \
                and ast.unparse(node.value).endswith("environ"):
            yield node.lineno, node.slice.value
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str) \
                and ast.unparse(node.func).endswith(("os.getenv", "environ.get")):
            yield node.lineno, node.args[0].value


def test_every_provider_key_variable_is_read_through_the_llm_presets():
    """One table maps a provider to its key variable: PROVIDER_PRESETS in llm/registry.py.

    Everything that needs a key -- the embeddings providers, the demo, the
    settings panel -- asks ``nl2sql.llm.providers`` for it.
    """
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(*SOURCES)
        if not path.is_relative_to(ENGINE / "llm")
        for lineno, name in _environment_reads(path)
        if PROVIDER_KEY_VARIABLE.match(name)
    ]
    assert not offenders, _broke(
        "A provider's API-key environment variable is read in nl2sql/llm/ only. The name "
        "lives in PROVIDER_PRESETS (llm/registry.py) and everything the CLI, the "
        "playground, evaluation and the REST API know about providers derives from it "
        "through llm/providers.py.", offenders)


# --------------------------------------------------------------------------
# 12. No process globals: the environment is read from an allow-list and
#     never written (docs/architecture/configuration_v2.md, sections 3 and 8)
# --------------------------------------------------------------------------

# Where ``os.environ``/``os.getenv`` may be read, and why. A folder entry ends
# in "/". Anything else is configuration, and is written in nl2sql.yaml, as
# ``${env:VAR}`` where it should come from the environment.
READS_THE_ENVIRONMENT = {
    "nl2sql/src/nl2sql/config/": "NL2SQL_CONFIG, and ${env:...} references",
    "nl2sql/src/nl2sql/llm/": "the provider key variables, through PROVIDER_PRESETS",
    "nl2sql/src/nl2sql/secrets/providers/env.py": "the env secret provider: ${env:...}",
    "nl2sql/src/nl2sql/common/logger.py": "NL2SQL_LOG_LEVEL",
    "nl2sql/src/nl2sql/tracing/trace.py": "the redactor masks every value the environment holds",
    "nl2sql/src/nl2sql/cli/commands/benchmark.py": "CI detection, not configuration",
}

# The one function that loads a .env file, and the entry points that call it.
LOADS_DOTENV = "nl2sql/src/nl2sql/config/loader.py"
ENTRY_POINTS_LOADING_DOTENV = {
    "nl2sql/src/nl2sql/cli/main.py",
    "nl2sql/src/nl2sql/cli/commands/demo.py",
    "api/src/nl2sql_api/main.py",
}

_ENVIRON_WRITERS = {"pop", "popitem", "setdefault", "update", "clear", "__setitem__", "__delitem__"}


def _is_environ(node: ast.AST) -> bool:
    return ast.unparse(node) in {"os.environ", "environ"}


def _environment_writes(path: pathlib.Path):
    """Yield ``(lineno, what)`` for every write to the process environment."""
    for node in ast.walk(_tree(path)):
        targets = []
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript) and _is_environ(target.value):
                yield node.lineno, ast.unparse(target)
            elif _is_environ(target):
                yield node.lineno, "os.environ = ..."
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _ENVIRON_WRITERS and _is_environ(func.value):
                yield node.lineno, ast.unparse(func)
            elif ast.unparse(func) in {"os.putenv", "os.unsetenv", "putenv", "unsetenv"}:
                yield node.lineno, ast.unparse(func)
            elif ast.unparse(func).endswith("load_dotenv") and _where(path) != LOADS_DOTENV:
                yield node.lineno, ast.unparse(func)


def _environment_access(path: pathlib.Path):
    """Yield ``(lineno, what)`` for every use of ``os.environ`` or ``os.getenv``."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Attribute) and ast.unparse(node) in {"os.environ", "os.getenv", "os.environb",
                                                                       "os.getenvb"}:
            yield node.lineno, ast.unparse(node)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in {"environ", "getenv", "environb", "getenvb", "putenv", "unsetenv"}:
                    yield node.lineno, f"from os import {alias.name}"


def _may_read_the_environment(path: pathlib.Path) -> bool:
    where = _where(path)
    return any(where.startswith(allowed) if allowed.endswith("/") else where == allowed
               for allowed in READS_THE_ENVIRONMENT)


def test_no_module_writes_the_environment():
    offenders = [f"{_where(path)}:{lineno} {what}"
                 for path in python_files(*SOURCES, SCRIPTS)
                 for lineno, what in _environment_writes(path)]
    assert not offenders, _broke(
        "No module, and no script under scripts/, writes the process environment: no os.environ "
        "assignment, pop or update, no os.putenv, no load_dotenv outside "
        "nl2sql.config.load_env_file. Keys and options travel in the engine's Config (a script "
        "passes them as arguments), so two engines in one process share nothing.", offenders)


def test_environment_reads_only_from_the_allow_list():
    offenders = [f"{_where(path)}:{lineno} {what}"
                 for path in python_files(*SOURCES) if not _may_read_the_environment(path)
                 for lineno, what in _environment_access(path)]
    assert not offenders, _broke(
        "The environment is read only for NL2SQL_CONFIG (nl2sql/config/), NL2SQL_LOG_LEVEL "
        "(configure_logging), the provider key variables (nl2sql/llm/), ${env:...} references, "
        "the trace redactor and CI detection. Anything else is configuration: put it in "
        "nl2sql.yaml, as ${env:VAR} if it should come from the environment.", offenders)


def test_only_entry_points_load_dotenv():
    loaders, callers = [], []
    for path in python_files(*SOURCES):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func).rpartition(".")[2]
            if name == "load_dotenv" and _where(path) != LOADS_DOTENV:
                loaders.append(f"{_where(path)}:{node.lineno} load_dotenv")
            elif name == "load_env_file" and _where(path) not in ENTRY_POINTS_LOADING_DOTENV:
                callers.append(f"{_where(path)}:{node.lineno} load_env_file")
    assert not loaders + callers, _broke(
        "Only the entry points -- the CLI, nl2sql demo and nl2sql-api -- load a .env file, through "
        "nl2sql.config.load_env_file, the one place that calls load_dotenv. The library never "
        "reads a .env file; a Python caller who wants one loads it before NL2SQL.from_config().",
        loaders + callers)


def test_the_settings_global_is_gone():
    assert not (ENGINE / "common" / "settings.py").exists()
    assert not (ENGINE / "common" / "env_hint.py").exists()
