"""The guided dashboard builder, engine side.

Two steps, no second pipeline. ``propose`` is read-only: it turns the person's picks into a list of tiles to
tick, and calls a model at most once, only for "tell us what matters". ``build`` takes the ticked list and makes an
ordinary dashboard with the edits every caller already has: a curated or saved tile is one no-model edit, and a new
question is an ``ask`` step of one builder conversation joined by ``add_tile``. Every tile is checked as the caller
before it is kept, so a question the engine cannot answer is skipped with its reason and the rest are built.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Literal, Optional, Sequence

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from nl2sql.api.types import TileProgress
from nl2sql.common.cancellation import CancellationToken, RunCancelled
from nl2sql.common.errors import InvalidArgument, NL2SQLError, NoTiles, WorkspaceNotEmpty
from nl2sql.common.strict import StrictModel
from nl2sql.dashboards.models import (
    AddSavedTile, AddTile, AddVerifiedTile, ChartChoice, Dashboard, DashboardFilter, SetFilters, SetTileChart,
    StepRef,
)
from nl2sql.dashboards.roles import Source, column_roles
from nl2sql.dashboards.templates import _cap, _slug, _words
from nl2sql.events.catalog import DashboardGenerated
from nl2sql.knowledge.resolve import resolve_column
from nl2sql.llm.override import LLMOverride
from nl2sql.llm.request_key import MissingProviderKey, current_request_llms, use_llm_override
from nl2sql.policies import UserContext

logger = logging.getLogger(__name__)

MAX_ASKED = 3  # questions that go to the model
AGENT = "conversation"  # the role the one model call runs as: it already answers for the person in chat
TileSource = Literal["curated", "saved", "asked"]
SOURCES = ("curated", "saved", "asked")


class TimeRange(StrictModel):
    id: str
    label: str


TIME_RANGES = (TimeRange(id="last_7_days", label="last 7 days"), TimeRange(id="last_30_days", label="last 30 days"),
               TimeRange(id="last_90_days", label="last 90 days"), TimeRange(id="last_12_months", label="last 12 months"),
               TimeRange(id="this_year", label="this year"))


class BuilderQuestion(StrictModel):
    """A curated query or a saved answer the person may tick."""

    id: str
    question: str
    datasource_id: str


class MetricOption(StrictModel):
    """A glossary term that names columns the caller can read."""

    term: str
    columns: List[str]  # "datasource.schema.table.column"
    definition: Optional[str] = None


class ColumnOption(StrictModel):
    label: str
    column: str  # "datasource.schema.table.column"
    table: str


class BuilderOptions(StrictModel):
    """The chips of the builder: everything on it exists and is readable by the caller."""

    curated: List[BuilderQuestion] = Field(default_factory=list)
    saved: List[BuilderQuestion] = Field(default_factory=list)
    metrics: List[MetricOption] = Field(default_factory=list)
    dimensions: List[ColumnOption] = Field(default_factory=list)
    time_columns: List[ColumnOption] = Field(default_factory=list)
    ranges: List[TimeRange] = Field(default_factory=lambda: list(TIME_RANGES))


class TileProposal(StrictModel):
    """One tile to tick, untick or reword. ``ref`` is the verified or saved id; a new question has none."""

    source: TileSource
    ref: Optional[str] = None
    question: str
    title: str = ""  # else made from the question
    chart: ChartChoice = "auto"
    ticked: bool = True


class SkippedQuestion(StrictModel):
    question: str
    code: str
    message: str


class BuildReport(StrictModel):
    dashboard: Dashboard
    built: List[str]  # tile ids, in order
    skipped: List[SkippedQuestion] = Field(default_factory=list)


class BuilderQuestions(BaseModel):
    """What the one model call answers with."""

    questions: List[str] = Field(description="Plain-language questions about the data, one chart each.")


PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You help a person who is not an analyst build a dashboard over their own data. Write up to {count} "
               "short, plain-language questions, each answerable by one chart from the tables below. Cover what the "
               "person said matters; do not repeat a question; do not write SQL. Only use these tables:\n{tables}\n"
               "Answer with the questions only."),
    ("human", "What matters: {matters}\nBreak down by: {breakdowns}\nOver: {period}\nIn their words: {text}")])


def short_title(question: str) -> str:
    """A chart title from a question: its first words, without the question mark."""
    text = question.strip().rstrip("?.! ").strip()
    return text if len(text) <= 60 else text[:57].rstrip() + "..."


def full_name(source: Source, schema: Optional[str], table: str, column: str) -> str:
    return f"{source.datasource_id}.{schema}.{table}.{column}"


class Builder:
    """What ``engine.dashboards.builder_options``, ``propose_tiles`` and ``build`` do. ``Dashboards`` owns it."""

    def __init__(self, ctx: Any, dashboards: Any) -> None:
        self._ctx = ctx
        self._dashboards = dashboards

    # ------------------------------------------------------------ what the caller may pick

    def _curated(self, source: Source, user: UserContext) -> List[Any]:
        from nl2sql.api.curated_api import CuratedAPI

        return [q for q in CuratedAPI(self._ctx).list(user_context=user, datasource_id=source.datasource_id)
                if not q.required_params]  # a query that needs a value is not offered: nothing here asks for one

    def _saved(self, source: Source, user: UserContext) -> List[Any]:
        from nl2sql.answers.service import Answers

        return Answers(self._ctx).list(user_context=user, datasource_id=source.datasource_id)

    @staticmethod
    def _metrics(source: Source) -> List[MetricOption]:
        out: List[MetricOption] = []
        for term in source.glossary.terms:
            columns: List[str] = []
            for name in term.columns:
                table, column, _ = resolve_column(name, source.snapshot)
                if table is None or column is None or not source.allows(table.table.schema_name,
                                                                        table.table.table_name):
                    columns = []
                    break  # a term that touches something the caller cannot read is left out whole
                columns.append(full_name(source, table.table.schema_name, table.table.table_name, column.name))
            if columns:
                out.append(MetricOption(term=term.term, columns=columns, definition=term.definition))
        return out

    @staticmethod
    def _column_options(source: Source):
        dimensions: List[ColumnOption] = []
        times: List[ColumnOption] = []
        for table in column_roles(source):
            ref = table.contract.table
            label = _cap(_words(table.name))
            for col in table.dimensions:
                dimensions.append(ColumnOption(label=_cap(_words(col.name)), table=label,
                                               column=full_name(source, ref.schema_name, ref.table_name, col.name)))
            for col in table.times:
                times.append(ColumnOption(label=_cap(_words(col.name)), table=label,
                                          column=full_name(source, ref.schema_name, ref.table_name, col.name)))
        return dimensions, times

    def options(self, source: Source, user: UserContext) -> BuilderOptions:
        dimensions, times = self._column_options(source)
        return BuilderOptions(
            curated=[BuilderQuestion(id=q.id, question=q.question, datasource_id=q.datasource_id)
                     for q in self._curated(source, user)],
            saved=[BuilderQuestion(id=a.id, question=a.question, datasource_id=a.datasource_id)
                   for a in self._saved(source, user)],
            metrics=self._metrics(source), dimensions=dimensions, time_columns=times)

    # ------------------------------------------------------------ propose (read-only)

    def propose(self, source: Source, user: UserContext, *, sources: Sequence[str], curated_ids: Sequence[str],
                saved_ids: Sequence[str], metrics: Sequence[str], dimensions: Sequence[str],
                time_range: Optional[str], text: Optional[str],
                llm_override: Optional[LLMOverride]) -> List[TileProposal]:
        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            raise InvalidArgument(f"Unknown source {unknown[0]!r}; choose from {', '.join(SOURCES)}.")
        label = None
        if time_range is not None:
            label = next((r.label for r in TIME_RANGES if r.id == time_range), None)
            if label is None:
                raise InvalidArgument(f"Unknown time range {time_range!r}; choose from "
                                      f"{', '.join(r.id for r in TIME_RANGES)}.")
        cap = self._ctx.config.dashboards.max_tiles
        out: List[TileProposal] = []
        if "curated" in sources:
            offered = {q.id: q for q in self._curated(source, user)}
            out += [TileProposal(source="curated", ref=i, question=offered[i].question,
                                 title=short_title(offered[i].question)) for i in curated_ids if i in offered]
        if "saved" in sources:
            mine = {a.id: a for a in self._saved(source, user)}
            out += [TileProposal(source="saved", ref=i, question=mine[i].question, chart=mine[i].chart,
                                 title=short_title(mine[i].question)) for i in saved_ids if i in mine]
        out = out[:cap]
        room = min(MAX_ASKED, cap - len(out))
        said = [x for x in (*metrics, *dimensions, (text or "").strip()) if x]
        if "asked" in sources and room > 0 and said:  # nothing to go on: no model call
            asked = self._ask_model(source, user, metrics, dimensions, label, text, room, llm_override)
            out += [TileProposal(source="asked", question=q, title=short_title(q)) for q in asked]
        return out

    def _ask_model(self, source: Source, user: UserContext, metrics: Sequence[str], dimensions: Sequence[str],
                   period: Optional[str], text: Optional[str], count: int,
                   llm_override: Optional[LLMOverride]) -> List[str]:
        from nl2sql.llm.wires import structured

        offered = {m.term.lower(): m for m in self._metrics(source)}
        matters = [offered[m.lower()].term for m in metrics if m.lower() in offered]
        by_column = {d.column: d for d in self._column_options(source)[0]}
        breakdowns = [by_column[c].label for c in dimensions if c in by_column]
        tables = ", ".join(t.name for t in column_roles(source)) or "none"
        registry = self._ctx.llm_registry
        with use_llm_override(llm_override, registry):
            llm = self._model(registry)
            chain = PROMPT | structured(llm, BuilderQuestions)
            answer = chain.invoke({
                "count": count, "tables": tables, "matters": ", ".join(matters) or "not said",
                "breakdowns": ", ".join(breakdowns) or "not said", "period": period or "not said",
                "text": (text or "").strip() or "nothing more"})
        questions: List[str] = []
        for question in (answer.questions if answer is not None else []):
            question = question.strip()
            if question and question.lower() not in {q.lower() for q in questions}:
                questions.append(question)
        return questions[:count]

    def _model(self, registry: Any) -> Any:
        """The model for the one call, or the missing-key error when this caller has no key for it."""
        config = registry.get_llm_config(AGENT)
        if config.get("provider") == "replay" and current_request_llms() is None:
            # The keyless hosted demo: replay needs no key, but it has recorded nothing for new questions.
            raise MissingProviderKey(AGENT, "replay")
        try:
            return registry.get_llm(AGENT)
        except ValueError:
            config = registry.get_llm_config(AGENT)
            raise MissingProviderKey(AGENT, str(config.get("provider") or "the configured provider")) from None

    # ------------------------------------------------------------ build

    def build(self, source: Source, tiles: Sequence[TileProposal], user: UserContext, *, conversations: Any,
              title: Optional[str], workspace_id: Optional[str], time_column: Optional[str],
              on_tile: Optional[Callable[[TileProgress], None]], token: Optional[CancellationToken],
              llm_override: Optional[LLMOverride]) -> BuildReport:
        ticked = [t for t in tiles if t.ticked]
        cap = self._ctx.config.dashboards.max_tiles
        if len(ticked) > cap:
            raise InvalidArgument(f"A dashboard holds at most {cap} charts (dashboards.max_tiles); {len(ticked)} were "
                                  "ticked. Untick some.")
        if sum(1 for t in ticked if t.source == "asked") > MAX_ASKED:
            raise InvalidArgument(f"At most {MAX_ASKED} new questions go to the model in one build; untick some.")
        if time_column is not None and time_column not in {t.column for t in self._column_options(source)[1]}:
            raise InvalidArgument(f"'{time_column}' is not a time column you can read here.")
        if any(t.source == "asked" for t in ticked):
            with use_llm_override(llm_override, self._ctx.llm_registry):
                self._model(self._ctx.llm_registry)  # a missing key is refused before anything is made
        dashboards = self._dashboards
        owner_workspaces = {w.id for w in dashboards._store.workspaces(dashboards._owner(user))}
        draft = dashboards.create_draft(user_context=user, title=(title or "").strip() or f"{source.datasource_id} dashboard",
                                        workspace_id=workspace_id)
        made_workspace = draft.workspace_id not in owner_workspaces
        conversation_id: Optional[str] = None
        built: List[str] = []
        skipped: List[SkippedQuestion] = []
        try:
            for done, tile in enumerate(ticked, 1):
                if token is not None and token.is_cancelled():
                    raise RunCancelled("The dashboard build was cancelled.")
                answered = False
                try:
                    if tile.source == "asked" and conversation_id is None:
                        conversation_id = conversations.start(user_context=user,
                                                              datasource_id=source.datasource_id).id
                    after = self._add(draft.id, tile, source, user, conversations, conversation_id, llm_override, token)
                    built.append(after.tiles[-1].id)
                    answered = True
                except (NL2SQLError, ValueError, LookupError) as error:
                    skipped.append(SkippedQuestion(question=tile.question, code=getattr(error, "code", None)
                                                   or "NOT_BUILT", message=str(error)))
                if on_tile is not None:
                    on_tile(TileProgress(title=tile.title or short_title(tile.question),
                                         status="ok" if answered else "skipped", done=done,
                                         total=len(ticked)))
            if not built:
                reasons = "; ".join(f"{s.question}: {s.message}" for s in skipped)
                raise NoTiles(f"None of the {len(ticked)} charts could be built ({reasons or 'none were ticked'}).")
            if time_column is not None:
                dashboards.edit(draft.id, [SetFilters(filters=[self._time_filter(source, time_column)])],
                                user_context=user)
            saved = dashboards.pin(draft.id, user_context=user)
        except BaseException:
            self._discard(draft, made_workspace, user)
            raise
        finally:
            if conversation_id is not None:
                try:
                    conversations.delete(conversation_id, user_context=user)
                except Exception:  # the build's outcome stands whatever happens to its scratch conversation
                    logger.warning("The builder conversation could not be removed.", exc_info=True)
        self._ctx.bus.publish("dashboard.generated", DashboardGenerated(
            dashboard_id=saved.id, datasource_id=source.datasource_id, tiles=len(saved.tiles), skipped=len(skipped)),
            tenant_id=dashboards._owner(user)[0])
        return BuildReport(dashboard=saved, built=built, skipped=skipped)

    def _add(self, dashboard_id: str, tile: TileProposal, source: Source, user: UserContext, conversations: Any,
             conversation_id: Optional[str], llm_override: Optional[LLMOverride],
             token: Optional[CancellationToken] = None) -> Dashboard:
        title = tile.title.strip() or short_title(tile.question)
        dashboards = self._dashboards
        failed: Optional[str] = None
        if tile.source == "curated":
            edits: List[Any] = [AddVerifiedTile(datasource_id=source.datasource_id, verified_id=tile.ref or "",
                                                title=title, question=tile.question or None)]
            conversation = None
        elif tile.source == "saved":
            edits, conversation = [AddSavedTile(saved_id=tile.ref or "", title=title)], None
        else:
            with use_llm_override(llm_override, self._ctx.llm_registry):
                result = conversations.ask_only(conversation_id, tile.question, user_context=user,
                                                cancellation_token=token)
            conversation = conversations.get(conversation_id, user_context=user)
            failed = (result.errors or [{}])[0].get("error_code") if result.status not in ("success", "plan_only")                 else None
            edits = [AddTile(title=title, from_step=StepRef(turn=conversation.turns[-1].index, step=1))]
        try:
            after = dashboards.edit(dashboard_id, edits, user_context=user, conversation=conversation)
        except InvalidArgument as error:
            if failed:  # the question's own reason, not the generic "did not answer"
                raise InvalidArgument(str(error), code=failed) from None
            raise
        if tile.chart != "auto":
            last = after.tiles[-1].id
            after = dashboards.edit(dashboard_id, [SetTileChart(tile_id=last, chart=tile.chart)], user_context=user)
        return after

    @staticmethod
    def _time_filter(source: Source, column: str) -> DashboardFilter:
        name = column.rsplit(".", 1)[-1]
        return DashboardFilter(id=_slug(name), label=_cap(_words(name)), column=column, kind="temporal", op="between")

    def _discard(self, draft: Dashboard, made_workspace: bool, user: UserContext) -> None:
        """Nothing half-made is left behind: the draft, and a workspace this build made."""
        dashboards = self._dashboards
        try:
            dashboards.delete(draft.id, user_context=user)
        except LookupError:
            pass
        if made_workspace:
            try:
                dashboards._workspaces.delete(draft.workspace_id, user_context=user)
            except (WorkspaceNotEmpty, LookupError):
                pass
