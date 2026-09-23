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
SDK = PACKAGES / "adapter-sdk" / "src" / "nl2sql_adapter_sdk"
ADAPTERS = ENGINE / "adapters"
SOURCES = [ENGINE, SDK, PACKAGES / "api" / "src" / "nl2sql_api"]


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
    return str(path.relative_to(PACKAGES)).replace("\\", "/")


def _broke(rule: str, offenders: list[str]) -> str:
    return f"{rule}\nThe rule is written down in {RULES}.\nBroken by:\n  " + "\n  ".join(offenders)


# --------------------------------------------------------------------------
# 1. The adapter SDK depends on nothing
# --------------------------------------------------------------------------

def test_the_adapter_sdk_imports_only_pydantic_and_the_stdlib():
    """An adapter author installs the SDK, not the engine."""
    allowed = {"pydantic", "nl2sql_adapter_sdk"} | set(sys.stdlib_module_names)
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(SDK)
        for lineno, name, _ in imports(path)
        if name.partition(".")[0] not in allowed
    ]
    assert not offenders, _broke(
        "The adapter SDK imports only pydantic and the standard library: it is the "
        "contract a third-party adapter compiles against, so it must not drag in the "
        "engine, SQLAlchemy or a driver.", offenders)


# --------------------------------------------------------------------------
# 2-4. The adapters are a leaf, and only they touch a database library
# --------------------------------------------------------------------------

# SQLAlchemy is the shared connection and reflection layer every bundled
# adapter is built on; sqlglot is the expression vocabulary the engine hands
# the adapter to render. Both are adapter-layer libraries, not engine ones.
ADAPTER_LIBRARIES = {"nl2sql_adapter_sdk", "pydantic", "sqlalchemy", "sqlglot", "nl2sql.adapters"}

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
        "the 'nl2sql.adapters' entry points and used through DatasourceAdapterProtocol, so "
        "a datasource the repo has never heard of works the same way the bundled ones do.",
        offenders)


def test_nothing_outside_the_adapters_imports_a_database_library():
    """SQLAlchemy in the pipeline would be a second, unpluggable way to reach a database."""
    offenders = []
    for path in python_files(*SOURCES):
        if path.is_relative_to(ADAPTERS):
            continue
        allowed = DATABASE_LIBRARY_EXCEPTIONS.get(_where(path), set())
        offenders += [f"{_where(path)}:{lineno} {name}"
                      for lineno, name, _ in imports(path)
                      if name.partition(".")[0] in DATABASE_LIBRARIES - allowed]
    assert not offenders, _broke(
        "Only nl2sql/adapters/ imports SQLAlchemy or a database driver. sqlite3 is not on "
        "this list: it is the standard library behind the engine's own schema and feedback "
        "stores, which are metadata, not a datasource.", offenders)


# --------------------------------------------------------------------------
# 5-6. Dialect knowledge lives in the adapter
# --------------------------------------------------------------------------

# Where a dialect name or a dialect's SQL would be a boundary break. The CLI is
# not here: it names adapters to install and configure them, which is registry
# knowledge, not SQL. Neither is datasets/, whose literals are file names.
DIALECT_SCAN = ["pipeline", "execution", "indexing", "evaluation", "api", "llm", "aggregation",
                "schema", "services", "datasources", "auth", "tracing", "context.py", "public_api.py"]

DIALECT_NAMES = re.compile(r"(?i)\b(sqlite|postgres|postgresql|mysql|mssql|tsql|duckdb|snowflake|bigquery)\b")

# Functions one database has and the next spells differently. The engine plans
# with portable operations (DATE_PART, DATE_TRUNC); rendering these is the
# adapter's job, through sqlglot or render_sql().
DIALECT_ONLY_SQL = re.compile(
    r"(?i)\b(STRFTIME|JULIANDAY|GETDATE|SYSDATE|DATEADD|DATEDIFF|DATEPART|DATE_FORMAT|TO_CHAR"
    r"|ILIKE|NVL|IIF|GROUP_CONCAT|STRING_AGG|LISTAGG)\b")

# Two things that carry a database's name without being a datasource: the
# engine's own metadata store, a SQLite file by default (a setting and the store
# that implements it), and the in-process polars/DuckDB compute engine that
# combines sub-query results already fetched through an adapter.
DIALECT_NAME_EXCEPTIONS = {
    "nl2sql/src/nl2sql/schema/store.py",
    "nl2sql/src/nl2sql/schema/sqlite_store.py",
    "nl2sql/src/nl2sql/common/settings.py",
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


def test_the_playground_never_touches_the_engine_context():
    """The playground is a client of the facade, exactly like nl2sql-api."""
    offenders = []
    for path in python_files(DEMO / "playground"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Attribute) and node.attr in {"context", "_ctx"}:
                offenders.append(f"{_where(path)}:{node.lineno} .{node.attr}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                  and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                  and node.args[1].value in {"context", "_ctx"}):
                offenders.append(f"{_where(path)}:{node.lineno} getattr(..., {node.args[1].value!r})")
    assert not offenders, _broke(
        "The playground reaches the engine only through NL2SQL's public methods, never "
        "engine.context. Anything it needs from the context is a facade method the REST "
        "API can call too.", offenders)


# --------------------------------------------------------------------------
# 9. The runtime does not pay for the benchmark (moved from
#    tests/unit/test_import_hygiene.py)
# --------------------------------------------------------------------------

RUNTIME = ["pipeline", "execution", "indexing", "datasources", "llm", "auth", "aggregation", "schema",
           "services", "context.py", "public_api.py"]


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
# 10. Provider knowledge has one home
# --------------------------------------------------------------------------

PROVIDER_KEY_VARIABLE = re.compile(r"^[A-Z0-9]+_API_KEY$")

KEY_READER_EXCEPTIONS = {
    # The embedding provider is a separate axis from the chat LLM:
    # EMBEDDING_PROVIDER picks it, and openai is one of its two values.
    "nl2sql/src/nl2sql/indexing/embeddings.py",
    # `demo --record` puts a recording proxy in front of the real provider and
    # points the config at it as `provider="openai"`, so the key the client
    # library looks for is OPENAI_API_KEY whatever the upstream was.
    "nl2sql/src/nl2sql/cli/commands/demo.py",
}


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

    ``common.settings.openai_api_key`` binds ``OPENAI_API_KEY`` as a pydantic
    validation alias rather than reading it, and is the declared settings
    surface; everything else that needs a key reads the preset.
    """
    offenders = [
        f"{_where(path)}:{lineno} {name}"
        for path in python_files(*SOURCES)
        if not path.is_relative_to(ENGINE / "llm") and _where(path) not in KEY_READER_EXCEPTIONS
        for lineno, name in _environment_reads(path)
        if PROVIDER_KEY_VARIABLE.match(name)
    ]
    assert not offenders, _broke(
        "A provider's API-key environment variable is read in nl2sql/llm/ only. The name "
        "lives in PROVIDER_PRESETS (llm/registry.py) and everything the CLI, the "
        "playground, evaluation and the REST API know about providers derives from it "
        "through llm/providers.py.", offenders)
