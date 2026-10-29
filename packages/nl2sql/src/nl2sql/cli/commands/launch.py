"""``nl2sql launch``: from a database to a working project in one sitting (docs/architecture/wave_c.md, section 7).

``nl2sql launch [DIR] [--provider NAME] [--as ROLE] [--draft] [--yes] [--no-ports]``. Every step is a package call;
this module only asks the questions and prints the answers:

1. the engine: a starter one for a new folder, the project's own (after its ``.env``) for an existing one;
2. find a database (``datasources.scan``, with ``DATABASE_URL`` as one candidate), fill what is missing, test it
   (``datasources.test``) until it connects;
3. the model key, from the environment or asked (hidden); a key or a password is written to ``.env``, never to YAML;
4. write the project (``write_project``) or the one datasource file (``datasources.save``), load the ``.env`` and
   rebuild the engine, so the key and every ``${env:...}`` reference resolve here and the written files are proven;
5. index, profile, file five starter questions as ``example`` proposals, and review the queue as an explicit admin.

``--yes`` accepts nothing: it exports ``review.yaml`` to decide in git. No model is called unless ``--draft``.
It is the one command that reads ``DATABASE_URL`` and the one that reloads the ``.env`` it has just written.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import click
import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.commands.knowledge import caller
from nl2sql.cli.commands.profile import profile_datasources
from nl2sql.cli.commands.review import _print_report
from nl2sql.cli.console import console
from nl2sql.llm.providers import KEYED_PROVIDERS, KEY_SHAPE_MESSAGE, api_key_from_env, env_var_for_provider, looks_like_api_key

if TYPE_CHECKING:
    from nl2sql import NL2SQL, DatasourceType, UserContext

REVIEW_FILE = "review.yaml"


def _say(text: str) -> None:
    console.print(text, soft_wrap=True)


def _fail(message: str) -> "typer.Exit":
    console.print(f"[bold red]Error:[/bold red] {escape(message)}", soft_wrap=True)
    return typer.Exit(1)


# ---------------------------------------------------------------- the engine


def _admin(engine: "NL2SQL", roles: Optional[List[str]]) -> "UserContext":
    """The admin the launch acts as: ``--as``, else the first of ``policies.admin_roles`` (none under ``allow_all``)."""
    chosen = list(roles or [])
    policies = engine.config.policies
    if not chosen and not policies.allow_all and policies.admin_roles:
        chosen = [policies.admin_roles[0]]
    try:
        return caller(engine, chosen, None)
    except ValueError as error:
        raise _fail(str(error))


def _reload(engine: "NL2SQL", config_path: pathlib.Path) -> "NL2SQL":
    """Load the ``.env`` next to ``config_path`` (never overriding a set variable) and rebuild the engine from the files."""
    from nl2sql import NL2SQL, ConfigError, load_env_file

    engine.close()
    load_env_file(config_path)
    try:
        return NL2SQL.from_config(config_path)
    except ConfigError as error:
        raise _fail(f"What was written does not load: {error}")


# ---------------------------------------------------------------- find, fill, test


def _where(found: Any) -> str:
    if found.kind == "url":
        return f"from DATABASE_URL, {found.value}"
    return f"{'file' if found.kind == 'file' else 'listening on'} {found.value}"


def _fixes(title: str, hint: str) -> None:
    """The two ways to get a missing driver: into this environment, or under ``uvx``'s throwaway one."""
    _say(f"[bold red]The {escape(title)} driver is not installed.[/bold red] Install it with:")
    _say(f"  {escape(hint)}")
    extra = re.search(r"nl2sql-engine\[[^\]]+\]", hint)
    if extra:
        _say("or, if you started with uvx, start again with it included:")
        _say(escape(f'  uvx --from "{extra.group(0)}" nl2sql launch'))


def _choose(engine: "NL2SQL", folder: pathlib.Path, no_ports: bool, types: Dict[str, "DatasourceType"]):
    """The database to connect: ``(type, connection, missing)`` of a candidate or of an adapter picked by name."""
    candidates = engine.datasources.scan(folder, url=os.environ.get("DATABASE_URL") or None, ports=not no_ports)
    _say("[bold]Databases found[/bold]")
    for number, candidate in enumerate(candidates, 1):
        _say(f"  {number}. {escape(candidate.title)}: {escape(_where(candidate.found))}")
    other = len(candidates) + 1
    _say(f"  {other}. Another database")
    number = typer.prompt("Which one?", type=click.IntRange(1, other), default=1)
    if number <= len(candidates):
        candidate = candidates[number - 1]
        if not candidate.installed:
            _fixes(candidate.title, candidate.install_hint)
            raise typer.Exit(1)
        return candidate.type, dict(candidate.connection), list(candidate.missing)
    name = typer.prompt("Which type?", type=click.Choice(sorted(types)), default="sqlite" if "sqlite" in types else None)
    if not types[name].installed:
        _fixes(types[name].title, types[name].install_hint)
        raise typer.Exit(1)
    return name, {}, list(types[name].connection_schema.get("required", []))


def _ask(key: str, spec: Dict[str, Any], current: Any) -> Any:
    """One connection field. A secret is hidden, and Enter keeps the one typed before."""
    kind = spec.get("type") or next((o.get("type") for o in spec.get("anyOf", []) if o.get("type") != "null"), "string")
    convert = {"integer": int, "number": float}.get(kind, str)
    secret = spec.get("format") == "password"
    return typer.prompt(spec.get("title") or key, default=current, hide_input=secret, show_default=not secret,
                        type=convert)


def _datasource_id(engine: "NL2SQL", type_name: str, connection: Dict[str, Any]) -> str:
    database = str(connection.get("database") or "")
    stem = pathlib.Path(database).stem if database and ("." in pathlib.Path(database).name) else database
    default = re.sub(r"[^a-z0-9_]+", "_", (stem or type_name).lower()).strip("_") or type_name
    taken = {d.id for d in engine.config.datasources}
    while True:
        chosen = typer.prompt("Name this datasource", default=default)
        if chosen not in taken:
            return chosen
        _say(f"[yellow]'{escape(chosen)}' is already a datasource here.[/yellow]")


def _connect(engine: "NL2SQL", folder: pathlib.Path, no_ports: bool):
    """Choose, fill and test until the database connects; returns the tested ``Datasource``."""
    from nl2sql import Datasource

    types = {t.name: t for t in engine.datasources.types()}
    type_name, connection, ask = _choose(engine, folder, no_ports, types)
    schema = types[type_name].connection_schema
    properties = schema.get("properties", {})
    datasource_id = None
    while True:
        for key in ask:
            connection[key] = _ask(key, properties.get(key, {}), connection.get(key))
        datasource_id = datasource_id or _datasource_id(engine, type_name, connection)
        datasource = Datasource(id=datasource_id, type=type_name, connection=dict(connection))
        status = engine.datasources.test(datasource)
        if status.status == "ready":
            _say(f"[green]Connected[/green] to {escape(datasource_id)}.")
            for warning in status.warnings:
                _say(f"[yellow]Warning:[/yellow] {escape(warning)}")
            return datasource
        stage = f" at {status.stage}" if status.stage else ""
        _say(f"[bold red]The connection failed{escape(stage)}:[/bold red] {escape(str(status.reason))}")
        if status.fix:
            _say(f"  Fix: {escape(status.fix)}")
        # Ask for every required field again, the typed values as defaults.
        ask = list(dict.fromkeys([*schema.get("required", []), *ask]))


def _model_key(provider: Optional[str]) -> Optional[str]:
    """The key to write to ``.env``, or None when the provider takes none or the environment has it already."""
    if provider not in KEYED_PROVIDERS:
        return None
    variable = env_var_for_provider(provider)
    if api_key_from_env(provider):
        _say(f"Using {variable} from the environment.")
        return None

    def checked(value: str) -> str:
        if not looks_like_api_key(value):
            raise click.UsageError(KEY_SHAPE_MESSAGE)
        return value.strip()

    return typer.prompt(f"{provider} API key (hidden; written to .env, never to YAML)", hide_input=True,
                        value_proc=checked)


# ---------------------------------------------------------------- write


def _write(engine: "NL2SQL", folder: pathlib.Path, config_path: pathlib.Path, datasource, provider: str,
           key: Optional[str], admin: "UserContext", exists: bool) -> None:
    from nl2sql import ConfigError, ReviewError
    from nl2sql.config import default_llm, starter_config, write_env_values, write_project

    try:
        if exists:
            engine.datasources.add(datasource)
            written = [engine.datasources.save(datasource.id, by=admin, env_file=True)]
            if key:
                written.append(write_env_values(folder, {env_var_for_provider(provider): key}))
        else:
            llm = default_llm(provider)
            if key:
                llm = llm.model_copy(update={"api_key": key})
            config = starter_config(llm=llm, path=folder / ".nl2sql").with_datasource(datasource)
            written = write_project(config, folder, env_file=True)
    except (FileExistsError, ValueError, PermissionError, ReviewError, ConfigError) as error:
        raise _fail(str(error))
    for path in written:
        _say(f"[green]wrote[/green] {escape(pathlib.Path(path).as_posix())}")


# ---------------------------------------------------------------- index, profile, starter questions, review


def _index(engine: "NL2SQL", datasource_id: str) -> None:
    def progress(step: Any) -> None:
        detail = f" {sum(step.counts.values())} entries" if step.counts else (f" {step.reason}" if step.reason else "")
        _say(f"  {escape(step.datasource_id)}: {step.state}{escape(detail)}")

    _say("\n[bold]Indexing[/bold] (the first run downloads the local embedding model)")
    result = engine.index.build([datasource_id], on_datasource=progress)
    if not result.ok:
        for error in result.errors:
            _say(f"[red]{escape(str(error.get('datasource_id')))}: {escape(str(error.get('error')))}[/red]")
        raise typer.Exit(1)


def _starter_questions(engine: "NL2SQL", datasource_id: str, admin: "UserContext") -> List[str]:
    from nl2sql import NotIndexed, ReviewError

    _say("\n[bold]Starter questions[/bold] (filed for review; nothing is accepted for you)")
    try:
        questions = engine.dashboards.suggest_questions(datasource_id, user_context=admin, n=5)
        engine.proposals.propose_examples(datasource_id, questions, by=admin)
    except (NotIndexed, ReviewError, ValueError) as error:
        _say(f"[yellow]No starter questions: {escape(str(error))}[/yellow]")
        return []
    for number, question in enumerate(questions, 1):
        _say(f"  {number}. {escape(question)}")
    return questions


def _review(engine: "NL2SQL", folder: pathlib.Path, datasource_id: str, admin: "UserContext", yes: bool) -> None:
    from nl2sql import ReviewError

    waiting = engine.proposals.list(user_context=admin, datasource_id=datasource_id)
    if not waiting:
        _say("\nNothing waits for review.")
        return
    if yes:
        path = folder / REVIEW_FILE
        count = engine.proposals.export(path, user_context=admin, datasource_id=datasource_id)
        _say(f"\n[bold]Review[/bold]: --yes accepts nothing. Wrote {count} proposals to {escape(path.as_posix())}; "
             f"set each decision, then run: nl2sql review apply {escape(path.as_posix())}")
        return
    _say(f"\n[bold]Review[/bold]: {len(waiting)} proposals. [a]ccept, [r]eject, [s]kip or [e]dit each.")
    accept: List[str] = []
    reject: List[str] = []
    edits: Dict[str, Dict[str, Any]] = {}
    for proposal in waiting:
        change = json.dumps(proposal.change, ensure_ascii=False, sort_keys=True, default=str)
        _say(f"\n{escape(proposal.kind)}  {escape(proposal.target)}  [dim]({escape(proposal.origin)})[/dim]\n"
             f"  [dim]{escape(change)}[/dim]")
        choice = typer.prompt("Decision", type=click.Choice(["a", "r", "s", "e"]), default="s")
        if choice == "e":
            try:
                edits[proposal.id] = json.loads(typer.prompt("The change, as JSON", default=change))
                choice = "a"
            except ValueError:
                _say("[yellow]That is not JSON; skipped.[/yellow]")
                continue
        if choice == "a":
            accept.append(proposal.id)
        elif choice == "r":
            reject.append(proposal.id)
    try:
        if accept:
            report = engine.proposals.accept(accept, by=admin, edits=edits or None)
            _print_report(report)
            if report.accepted:
                _say("Run `nl2sql index --refresh` so what you accepted reaches the index.")
        if reject:
            engine.proposals.reject(reject, by=admin)
            _say(f"[yellow]rejected[/yellow] {len(reject)}")
    except ReviewError as error:
        _say(f"[red]{escape(str(error))}[/red] {escape(error.fix or '')}")
    left = len(waiting) - len(accept) - len(reject)
    if left:
        _say(f"{left} left for later: nl2sql review list")


# ---------------------------------------------------------------- the command


def launch_command(
    ctx: typer.Context,
    directory: Annotated[pathlib.Path, typer.Argument(help="The project folder")] = pathlib.Path("."),
    provider: Annotated[str, typer.Option("--provider", help="The LLM provider, with its default model")] = "openai",
    as_: Annotated[Optional[List[str]], typer.Option(
        "--as", help="The admin role to act as (repeatable). Without one, the first of policies.admin_roles.")] = None,
    draft: Annotated[bool, typer.Option("--draft", help="Also draft descriptions with the model")] = False,
    yes: Annotated[bool, typer.Option(
        "--yes", help="Accept nothing: export the review file for review in git")] = False,
    no_ports: Annotated[bool, typer.Option("--no-ports", help="Do not look for databases on local ports")] = False,
) -> None:
    """Find a database, connect it, index it, and look at what was found."""
    from nl2sql import NL2SQL, ConfigError, load_env_file
    from nl2sql.config import default_llm, starter_config

    folder = directory
    config_path = folder / "nl2sql.yaml"
    exists = config_path.is_file()
    try:
        if exists:
            load_env_file(config_path)
            engine = NL2SQL.from_config(config_path)
            provider = engine.config.llm.provider or provider
        else:
            folder.mkdir(parents=True, exist_ok=True)
            # Only to scan and test: the glossary the written project reads does not exist yet.
            engine = NL2SQL(starter_config(llm=default_llm(provider), path=folder / ".nl2sql")
                            .model_copy(update={"knowledge": []}))
    except (ConfigError, ValueError) as error:
        raise _fail(str(error))
    try:
        admin = _admin(engine, as_)
        datasource = _connect(engine, folder, no_ports)
        key = None if exists and engine.config.llm.api_key is not None else _model_key(provider)
        _write(engine, folder, config_path, datasource, provider, key, admin, exists)
        engine = _reload(engine, config_path)
        admin = _admin(engine, as_)
        _index(engine, datasource.id)
        _say("\n[bold]Profiling[/bold]" + (" (drafting descriptions with the model)" if draft else ""))
        profile_datasources(engine, admin, [datasource.id], draft=draft)
        questions = _starter_questions(engine, datasource.id, admin)
        _review(engine, folder, datasource.id, admin, yes)
    finally:
        engine.close()

    where = "" if folder == pathlib.Path(".") else f"--config {config_path.as_posix()} "
    _say("\n[bold]Next[/bold]")
    if questions:
        _say(f"  nl2sql {escape(where)}run {escape(json.dumps(questions[0]))}")
    _say(f"  nl2sql demo --dir {escape(folder.as_posix())}")
