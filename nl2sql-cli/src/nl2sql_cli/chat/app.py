"""``nl2sql chat``: a keyboard-first terminal app over the facade (wave_d.md, section 7).

It imports only the top-level ``nl2sql`` and ``textual``. Every engine call names the caller (``UserContext``),
and a turn is streamed from ``engine.conversations.stream`` on a worker thread, so the screen never waits on the
model. The app keeps no state the engine does not: conversations, rows and approvals are the engine's.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Callable, List, Optional, Tuple

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, ListItem, ListView, Markdown, OptionList
from textual.widgets.option_list import Option

from nl2sql import (ChartSpec, ClarificationChoice, ClarificationRequest, NL2SQLError, QueryResult, ResultNotFound,
                    ToolOutput, ToolStep, Turn, UserContext)
from nl2sql_cli.chat.widgets import ChartView, Note, rows_table

PAGE = 50
HELP = ("Type a question and press Enter. Tab fills a suggested question. F2 says why this answer, F3 shows the SQL, "
        "PageDown reads more rows, Ctrl+N starts a new conversation, Ctrl+Q quits.\n"
        "/chart [line|bar|big_number|table]  /diagram  /report  /save FILE  /approvals  /remember  /new  /help\n"
        "Any other /name runs the chat tool of that name.")
COMMANDS = {"report", "save", "approvals", "remember", "new", "help"}


class ConversationItem(ListItem):
    def __init__(self, conversation_id: str, title: str) -> None:
        super().__init__(Label(title, markup=False))
        self.conversation_id = conversation_id


class ApproveButton(Button):
    """The button shown under one ``needs_approval`` result; it remembers the requests it was shown for."""

    def __init__(self, requests: List[Any]) -> None:
        super().__init__("Approve and run", classes="approve")
        self.requests = requests


class ChatApp(App[None]):
    TITLE = "nl2sql chat"
    CSS = """
    #conversations { width: 28; border-right: solid $primary; }
    #main { width: 1fr; }
    #transcript { height: 1fr; padding: 0 1; }
    #side { width: 48; padding: 0 1; border-left: solid $primary; display: none; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    .you { text-style: bold; margin-top: 1; }
    .reply { margin-top: 1; }
    .error { color: $error; }
    .warning { color: $warning; }
    .muted { color: $text-muted; }
    .big-number { text-style: bold; }
    .chart-title { text-style: bold; }
    .approve { margin: 1 0; }
    OptionList { height: auto; max-height: 8; margin: 1 0; }
    """
    BINDINGS = [
        Binding("f2", "side('why')", "Why this answer"),
        Binding("f3", "side('sql')", "SQL"),
        Binding("pagedown", "more_rows", "More rows"),
        Binding("ctrl+n", "new", "New conversation"),
        Binding("ctrl+o", "focus_conversations", "Conversations"),
        Binding("escape", "focus_composer", "Composer", show=False),
        Binding("tab", "next_question", "Suggested question", show=False, priority=True),
    ]

    def __init__(self, engine: Any, user: UserContext, *, datasource_id: Optional[str] = None) -> None:
        super().__init__()
        self.engine = engine
        self.user = user
        self.datasource_id = datasource_id
        self._conversation_id: Optional[str] = None
        self._busy = False
        self._last: Optional[QueryResult] = None
        self._table: Optional[Tuple[DataTable, str, str, Optional[int]]] = None
        self._request: Optional[ClarificationRequest] = None
        self._pick: Optional[Tuple[ClarificationRequest, str]] = None
        self._approvals: List[Any] = []
        self._report: Optional[str] = None
        self._questions: List[str] = []
        self._next_question = 0
        self._side_mode: Optional[str] = None

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(id="conversations")
            with Vertical(id="main"):
                yield VerticalScroll(id="transcript")
                yield Note("", id="status")
                yield Input(placeholder="Ask a question, or type / for commands", id="composer")
            yield Note("", id="side")
        yield Footer()

    def on_mount(self) -> None:
        self._questions = self._guided_questions()
        self._load_conversations()
        self._greet()
        self.query_one("#composer").focus()

    def _safe(self, call: Callable[[], Any], default: Any = None) -> Any:
        """Run an engine call from a key or message handler: an error is shown in the transcript, never a crash."""
        try:
            return call()
        except Exception as error:  # every NL2SQLError, and a store or driver failure
            self._fail(error)
            return default

    def _greet(self) -> None:
        roles = ", ".join(self.user.roles) or "no role"
        self._add(Note(f"Asking as {self.user.user_id or 'anonymous'} ({roles}). /help lists the commands.", classes="muted"))

    def _guided_questions(self) -> List[str]:
        found: List[str] = []
        for datasource in self._safe(lambda: self.engine.datasources.list(user_context=self.user), []):
            if self.datasource_id in (None, datasource.id):
                found += [q for q in datasource.sample_questions if q not in found]
        return found

    def _load_conversations(self) -> None:
        listing = self.query_one("#conversations", ListView)
        listing.clear()
        for info in self._safe(lambda: self.engine.conversations.list(user_context=self.user), []):
            listing.append(ConversationItem(info.id, info.title))

    # ------------------------------------------------------------------ the transcript

    def _add(self, widget: Any) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(widget)
        transcript.scroll_end(animate=False)

    def _status(self, text: str) -> None:
        self.query_one("#status", Note).set(text)

    def _fail(self, error: BaseException) -> None:
        fix = getattr(error, "fix", None)
        self._add(Note(f"Error: {error}" + (f"\n{fix}" if fix else ""), classes="error"))

    def _idle(self) -> None:
        self._busy = False
        self._status("")
        self._load_conversations()
        self.query_one("#composer").focus()

    def _show_turn(self, turn: Turn, *, with_question: bool = False) -> None:
        if with_question:
            self._add(Note(f"You: {turn.text}", classes="you"))
        listed = len(turn.steps) > 1 or any(step.call.name != "ask" for step in turn.steps)
        for number, step in enumerate(turn.steps, 1):
            if listed:
                self._add(Note(f"Step {number}: {step.call.name}: {step.question or ''} ({step.status})", classes="muted"))
            self._show_step(step, answer=turn.reply is None)
        if turn.reply:
            self._add(Note(turn.reply, classes="reply"))
        elif turn.error and not turn.steps:
            self._add(Note(str(turn.error.get("message") or turn.error), classes="error"))
        if turn.offers:
            self._add(Note("You can also run: " + ", ".join(f"/{offer.name}" for offer in turn.offers), classes="muted"))
        self._refresh_side()

    def _show_step(self, step: ToolStep, *, answer: bool) -> None:
        if step.output is not None:
            self._show_output(step.output)
        if step.error and step.result is None:
            self._add(Note(str(step.error.get("message") or step.error), classes="error"))
        result = step.result
        if result is None:
            return
        self._last = result
        text = (result.final_answer or {}).get("summary") or (result.final_answer or {}).get("content")
        if answer and text:
            self._add(Note(text, classes="reply"))
        for warning in result.warnings:
            if not (warning.detail or {}).get("recovered"):
                self._add(Note(f"Warning: {warning.message}", classes="warning"))
        self._show_rows(step, result)
        if result.clarification is not None:
            self._ask_which(result.clarification)
        if result.status == "needs_approval":
            self._ask_approval(result)

    def _show_output(self, output: ToolOutput) -> None:
        if output.kind == "chart":
            self._add(ChartView(ChartSpec.model_validate(output.body)))
        elif output.kind == "markdown":
            self._add(Markdown(str(output.body)))
        elif output.kind == "mermaid":
            self._add(Note(f"{output.title} (Mermaid text; a terminal cannot draw it)\n{output.body}"))
        else:
            self._add(Note(json.dumps(output.body, indent=2, default=str)))

    def _show_rows(self, step: ToolStep, result: QueryResult) -> None:
        for summary in result.result_manifest if step.trace_id else []:
            try:
                page = self.engine.results.page(step.trace_id, summary.node_id, 0, PAGE, user_context=self.user)
            except ResultNotFound:
                self._add(Note("The rows of this answer are no longer available.", classes="muted"))
                return
            table = rows_table(page.columns, page.rows)
            self._add(table)
            self._table = (table, step.trace_id, summary.node_id, page.next_offset)
            if page.next_offset is not None:
                self._add(Note(f"{page.total_rows} rows. PageDown reads more.", classes="muted"))

    # ------------------------------------------------------------------ clarification and approval

    def _ask_which(self, request: ClarificationRequest) -> None:
        self._request = request
        self._add(Note(f"\"{request.phrase}\" can be read more than one way. Pick one:"))
        self._add(OptionList(*[Option(option.label, id=option.id) for option in request.options]))

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        request, option_id = self._request, event.option.id
        if request is None or option_id is None or self._busy:
            return
        label = next(o.label for o in request.options if o.id == option_id)
        self._request, self._pick = None, (request, option_id)
        event.option_list.disabled = True
        self._ask(None, choice=option_id, shown=label)

    def _ask_approval(self, result: QueryResult) -> None:
        self._approvals = list(result.approvals_needed)
        for request in self._approvals:
            self._add(Note(f"Needs approval: {request.reason}\n{request.sql}", classes="warning"))
        roles = sorted({role for request in self._approvals for role in request.approver_roles})
        if roles and not set(roles) & set(self.user.roles):
            self._add(Note(f"Only {', '.join(roles)} can approve this. An approver can list it with /approvals.", classes="muted"))
        else:
            self._add(ApproveButton(self._approvals))

    @on(Button.Pressed, ".approve")
    def _approve(self, event: Button.Pressed) -> None:
        button = event.button
        if self._busy or not isinstance(button, ApproveButton) or self._conversation_id is None:
            return
        button.disabled = True
        requests, conversation_id = list(button.requests), self._conversation_id
        self._run(lambda: self._grant_and_retry(conversation_id, requests), "Approving")

    def _grant_and_retry(self, conversation_id: str, requests: List[Any]) -> Turn:
        for request in requests:
            self.engine.approvals.grant(request.id, by=self.user)
        return self.engine.conversations.retry(conversation_id, user_context=self.user,
                                               approvals=[request.id for request in requests])

    # ------------------------------------------------------------------ input

    @on(Input.Submitted, "#composer")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._command(text[1:].split())
        else:
            self._ask(text)

    def _ask(self, text: Optional[str], *, choice: Optional[str] = None, shown: Optional[str] = None) -> None:
        self._add(Note(f"You: {shown or text}", classes="you"))
        self._run(lambda: self._stream(text, choice), "Thinking")

    def _command(self, words: List[str]) -> None:
        name, args = (words[0] if words else "help"), words[1:]
        if name not in COMMANDS:
            if self._conversation_id is None:
                self._add(Note("Ask a question first: a tool works on a conversation.", classes="muted"))
                return
            self._add(Note(f"You: /{name}", classes="you"))
            conversation_id = self._conversation_id
            tool_args = {"kind": args[0]} if name == "chart" and args else {}
            self._run(lambda: self.engine.conversations.tool(conversation_id, name, tool_args, user_context=self.user),
                      f"Running {name}")
        elif name == "help":
            self._add(Note(HELP, classes="muted"))
        elif name == "new":
            self.action_new()
        elif name == "approvals":
            self._list_approvals()
        elif name == "remember":
            self._remember()
        elif name == "report":
            self._show_report()
        else:
            self._save(args)

    def _list_approvals(self) -> None:
        records = self._safe(lambda: self.engine.approvals.list(user_context=self.user))
        if records is None:
            return
        if not records:
            self._add(Note("No approvals are waiting.", classes="muted"))
        for record in records:
            request = record.request
            self._add(Note(f"{record.id[:8]}  {request.question}: {request.reason}\n{request.sql}"))

    def _remember(self) -> None:
        if self._pick is None:
            self._add(Note("Nothing to remember: pick a reading first.", classes="muted"))
            return
        request, option_id = self._pick
        try:
            self.engine.knowledge.save_choice(ClarificationChoice(request=request, option_id=option_id), user_context=self.user)
        except Exception as error:  # a ReviewError or NotAuthorized: shown, never a crash
            self._fail(error)
            return
        self._add(Note("Saved as a proposal for review. It reaches the glossary when an admin accepts it.", classes="muted"))

    def _show_report(self) -> None:
        if self._conversation_id is None:
            self._add(Note("Ask a question first: a report is made from a conversation.", classes="muted"))
            return
        conversation_id = self._conversation_id
        report = self._safe(lambda: self.engine.conversations.report(conversation_id, user_context=self.user))
        if report is None:
            return
        self._report = report
        self._add(Markdown(self._report))
        self._add(Note("/save FILE writes this report to a file.", classes="muted"))

    def _save(self, args: List[str]) -> None:
        if self._report is None or not args:
            self._add(Note("Run /report first, then /save FILE.", classes="muted"))
            return
        path = pathlib.Path(" ".join(args))
        try:
            path.write_text(self._report, encoding="utf-8")
        except OSError as error:
            self._fail(error)
            return
        self._add(Note(f"Saved {path}.", classes="muted"))

    # ------------------------------------------------------------------ one turn at a time, off the screen's thread

    def _run(self, call: Callable[[], Turn], status: str) -> None:
        if self._busy:
            self._add(Note("A turn is still running.", classes="muted"))
            return
        self._busy = True
        self._status(f"{status}...")
        self._work(call)

    @work(thread=True)
    def _work(self, call: Callable[[], Turn]) -> None:
        try:
            turn = call()
            self.call_from_thread(self._show_turn, turn)
        except Exception as error:  # every NL2SQLError, and a driver or model failure: shown in the transcript
            self.call_from_thread(self._fail, error)
        finally:
            self.call_from_thread(self._idle)

    def _stream(self, text: Optional[str], choice: Optional[str]) -> Turn:
        engine = self.engine.conversations
        if self._conversation_id is None:
            self._conversation_id = engine.start(user_context=self.user, datasource_id=self.datasource_id).id
        conversation_id = self._conversation_id
        stored = None
        for event in engine.stream(conversation_id, text, user_context=self.user, choice=choice):
            if event.name == "node.started":
                self.call_from_thread(self._status, f"Running {event.data.get('node')}...")
            elif event.name == "conversation.step":
                self.call_from_thread(self._status, f"Step: {event.data.get('tool')} ({event.data.get('status')})")
            elif event.name == "conversation.turn":
                stored = event.data.get("stored")
            elif event.name == "run.failed":
                raise RuntimeError(event.data.get("message") or "The run failed.")
        if stored is None:
            raise RuntimeError("The conversation ended without a turn.")
        return Turn.model_validate(stored)

    # ------------------------------------------------------------------ keys

    def action_next_question(self) -> None:
        composer = self.query_one("#composer", Input)
        if self.focused is composer and self._questions:
            composer.value = self._questions[self._next_question % len(self._questions)]
            composer.cursor_position = len(composer.value)
            self._next_question += 1
        else:
            self.screen.focus_next()

    def action_focus_composer(self) -> None:
        self.query_one("#composer").focus()

    def action_focus_conversations(self) -> None:
        self.query_one("#conversations").focus()

    def action_new(self) -> None:
        if self._busy:
            self._add(Note("A turn is still running.", classes="muted"))
            return
        self._reset()

    def _reset(self) -> None:
        self._conversation_id, self._last, self._table, self._request = None, None, None, None
        self._pick, self._report, self._approvals = None, None, []
        self.query_one("#transcript").remove_children()
        self._refresh_side()
        self._greet()

    def action_more_rows(self) -> None:
        if self._table is None:
            return
        table, run_id, result_id, offset = self._table
        if offset is None:
            self._status("That is every row.")
            return
        page = self._safe(lambda: self.engine.results.page(run_id, result_id, offset, PAGE, user_context=self.user))
        if page is None:
            return
        table.add_rows([tuple("" if cell is None else str(cell) for cell in row) for row in page.rows])
        table.styles.height = min(table.row_count + 2, 14)
        self._table = (table, run_id, result_id, page.next_offset)

    def action_side(self, mode: str) -> None:
        side = self.query_one("#side", Note)
        if side.display and self._side_mode == mode:
            side.display = False
            return
        self._side_mode = mode
        side.set(self._side_text())
        side.display = True

    def _refresh_side(self) -> None:
        side = self.query_one("#side", Note)
        if side.display:
            side.set(self._side_text())

    def _side_text(self) -> str:
        result = self._last
        if result is None:
            return "Nothing yet: ask a question first."
        if self._side_mode == "sql":
            return "\n\n".join(f"{sub.datasource_id}\n{sub.sql}" for sub in result.sub_queries if sub.sql) or "No SQL ran."
        explanation = result.explanation
        if explanation is None or not explanation.steps:
            return "This answer has no plan to explain."
        lines: List[str] = []
        for step in explanation.steps:
            lines.append(step.text)
            for label, items in (("Tables", step.tables), ("Joins", step.joins), ("Filters", step.filters),
                                 ("Grouped", step.groupings), ("Measures", step.measures),
                                 ("Order", [step.order] if step.order else []), ("Assumptions", step.assumptions)):
                if items:
                    lines.append(f"{label}: " + "; ".join(items))
            lines.append("")
        if explanation.combined:
            lines.append(explanation.combined)
        return "\n".join(lines).strip()

    @on(ListView.Selected, "#conversations")
    def _open(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, ConversationItem):
            return
        if self._busy:
            self._add(Note("A turn is still running.", classes="muted"))
            return
        conversation = self._safe(lambda: self.engine.conversations.get(item.conversation_id, user_context=self.user))
        if conversation is None:
            return
        self._reset()
        self._conversation_id = conversation.id
        for turn in conversation.turns:
            self._show_turn(turn, with_question=True)
        self.query_one("#composer").focus()
