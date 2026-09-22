"""The playground is a client of the public facade, like nl2sql-api.

* It never reaches past ``NL2SQL`` into ``engine.context`` (the ``NL2SQLContext``).
* ``nl2sql.cli.demo`` never imports ``nl2sql.cli.commands``: helpers shared with
  a command live in ``cli/demo/`` or ``cli/common/``.
"""
import ast
import pathlib

import nl2sql.cli.demo as demo_package

DEMO = pathlib.Path(demo_package.__file__).resolve().parent
PLAYGROUND = DEMO / "playground"


def _trees(root: pathlib.Path):
    for path in sorted(root.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_playground_never_touches_the_engine_context():
    offenders = []
    for path, tree in _trees(PLAYGROUND):
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"context", "_ctx"}:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                  and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                  and node.args[1].value in {"context", "_ctx"}):
                offenders.append(f"{path.name}:{node.lineno} getattr(..., {node.args[1].value!r})")
    assert not offenders, f"use NL2SQL's public methods instead: {offenders}"


def test_cli_demo_never_imports_cli_commands():
    offenders = []
    for path, tree in _trees(DEMO):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            offenders += [f"{path.relative_to(DEMO)}:{node.lineno} {name}" for name in names
                          if name == "nl2sql.cli.commands" or name.startswith("nl2sql.cli.commands.")]
    assert not offenders, f"move the shared helper out of cli/commands: {offenders}"
