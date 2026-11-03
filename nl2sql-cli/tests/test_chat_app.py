"""``nl2sql chat``: Textual's pilot over the app, with a stand-in engine behind the public facade."""
from __future__ import annotations

import asyncio
import subprocess
import sys

from chat_fakes import FakeEngine, approval_request, ask, chart_step, clarification, result, turn
from textual.widgets import DataTable, Markdown, OptionList, Sparkline

from nl2sql import ChartData, ChartSpec, Encoding, InvalidArgument, ResultNotFound, UserContext
from nl2sql_cli.chat.app import ChatApp


def run(engine, scenario, *, user=None):
    """Start the app on ``engine``, hand its pilot to ``scenario`` and close."""
    app = ChatApp(engine, user or UserContext(user_id="ana", tenant_id="t", roles=["analyst"]))

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await scenario(app, pilot)

    asyncio.run(go())
    return app


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


async def say(app, pilot, text):
    composer = app.query_one("#composer")
    composer.value = text
    composer.focus()
    await pilot.press("enter")
    await settle(app, pilot)


def transcript(app) -> str:
    return "\n".join(w.text for w in app.query("#transcript Note"))


def test_chat_streams_a_turn_and_shows_rows():
    engine = FakeEngine([turn(ask())])

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        text = transcript(app)
        assert "customers by country" in text and "Most customers are in the USA." in text
        [table] = app.query(DataTable)
        assert table.row_count == 2 and [str(c.label) for c in table.columns.values()] == ["country", "customers"]

    run(engine, scenario)
    kind, said, choice, caller = engine.conversations.calls[1]
    assert (kind, said, choice) == ("stream", "customers by country", None)
    assert caller.user_id == "ana" and caller.roles == ["analyst"]
    assert engine.page_calls[0][:3] == ("t1", "n1", 0) and engine.page_calls[0][3] is caller


def test_page_down_reads_the_next_page_into_the_same_table():
    engine = FakeEngine([turn(ask())])

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        await pilot.press("pagedown")
        await settle(app, pilot)
        [table] = app.query(DataTable)
        assert table.row_count == 3

    run(engine, scenario)
    assert [c[2] for c in engine.page_calls] == [0, 2]


def test_follow_up_shows_the_models_steps():
    engine = FakeEngine([turn(ask(question="customers by country"), ask(question="only in Europe"), reply="Done.")])

    async def scenario(app, pilot):
        await say(app, pilot, "and in Europe?")
        text = transcript(app)
        assert "ask: customers by country" in text and "ask: only in Europe" in text

    run(engine, scenario)


def test_f2_shows_why_this_answer_and_f3_the_sql():
    engine = FakeEngine([turn(ask())])

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        side = app.query_one("#side")
        assert not side.display
        await pilot.press("f2")
        assert side.display and "Customer" in side.text and "by Country" in side.text
        await pilot.press("f3")
        assert "SELECT country" in side.text
        await pilot.press("f3")
        assert not side.display

    run(engine, scenario)


def _line_spec() -> dict:
    return ChartSpec(kind="line", title="customers by country", reason="r", takeaway="Up and to the right.",
                     x=Encoding(field="month", kind="temporal", title="Month"),
                     y=[Encoding(field="revenue", kind="number", title="Revenue")],
                     data=ChartData(trace_id="t1", result_id="n1", columns=["month", "revenue"],
                                    rows=[["2024-01", 1], ["2024-02", 4], ["2024-03", 2]],
                                    truncated=False)).model_dump(mode="json")


def test_chart_tool_draws_sparklines_for_a_line():
    engine = FakeEngine([turn(ask()), turn(chart_step(_line_spec()), reply=None, index=2, text="chart")])

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        await say(app, pilot, "/chart")
        assert len(app.query(Sparkline)) == 1
        assert "Up and to the right." in transcript(app)

    run(engine, scenario)
    assert engine.conversations.calls[-1] == ("tool", "chart", {})


def test_diagram_shows_the_mermaid_text():
    diagram = chart_step("flowchart LR\n  a --> b", name="diagram", kind="mermaid")
    engine = FakeEngine([turn(ask()), turn(diagram, reply=None, index=2, text="diagram")])

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        await say(app, pilot, "/diagram")
        assert "a --> b" in transcript(app)

    run(engine, scenario)
    assert engine.conversations.calls[-1][:2] == ("tool", "diagram")


def test_report_shows_markdown_and_is_saved_to_the_file_the_user_names(tmp_path):
    engine = FakeEngine([turn(ask())])
    target = tmp_path / "report.md"

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        await say(app, pilot, "/report")
        assert len(app.query(Markdown)) == 1
        await say(app, pilot, f"/save {target}")

    run(engine, scenario)
    assert target.read_text(encoding="utf-8").startswith("# Report")


def test_clarification_pick_asks_with_choice():
    pending = clarification()
    asked = turn(ask(result(status="needs_clarification", clarification=pending, answer=None),
                     status="needs_clarification"), status="needs_clarification", reply=None, text="revenue by country")
    engine = FakeEngine([asked, turn(ask(), index=2, text="Revenue means track sales")])
    engine.conversations.pending = pending

    async def scenario(app, pilot):
        await say(app, pilot, "revenue by country")
        [options] = app.query(OptionList)
        assert options.option_count == 2
        options.highlighted = 1
        options.action_select()
        await settle(app, pilot)
        await say(app, pilot, "/remember")
        await settle(app, pilot)

    run(engine, scenario)
    kind, said, choice, _ = engine.conversations.calls[-1]
    assert (kind, said, choice) == ("stream", None, "b")
    [saved] = engine.saved_choices
    assert saved.option_id == "b" and saved.request.id == pending.id


def _needs_approval():
    pending = approval_request()
    asked = turn(ask(result(status="needs_approval", approvals_needed=[pending], answer=None, result_manifest=[]), status="needs_approval"),
                 status="needs_approval", reply=None)
    return asked, pending


def test_approver_can_approve_and_retry():
    asked, pending = _needs_approval()
    engine = FakeEngine([asked, turn(ask(), index=2, text="customers")])
    admin = UserContext(user_id="ana", tenant_id="t", roles=["admin"])

    async def scenario(app, pilot):
        await say(app, pilot, "customers")
        text = transcript(app)
        assert "Reads customer data" in text and "SELECT * FROM customer" in text
        await pilot.click(".approve")
        await settle(app, pilot)
        assert len(app.query(DataTable)) == 1

    run(engine, scenario, user=admin)
    assert engine.granted == [pending.id]
    assert engine.conversations.calls[-1] == ("retry", [pending.id])


def test_a_user_who_cannot_approve_is_told_who_can():
    asked, _ = _needs_approval()
    engine = FakeEngine([asked])

    async def scenario(app, pilot):
        await say(app, pilot, "customers")
        assert not app.query(".approve")
        assert "Only admin can approve" in transcript(app)

    run(engine, scenario)
    assert engine.granted == [] and not any(c[0] == "retry" for c in engine.conversations.calls)


def test_approvals_command_lists_pending_requests():
    engine = FakeEngine([])

    async def scenario(app, pilot):
        await say(app, pilot, "/approvals")
        assert "Reads customer data" in transcript(app)

    run(engine, scenario)


def test_tab_in_the_composer_offers_the_guided_questions_in_turn():
    engine = FakeEngine([])

    async def scenario(app, pilot):
        composer = app.query_one("#composer")
        composer.focus()
        await pilot.press("tab")
        assert composer.value == "How many customers are there?"
        await pilot.press("tab")
        assert composer.value == "Revenue by year?"

    run(engine, scenario)


def test_an_engine_error_is_shown_in_the_transcript():
    engine = FakeEngine([])

    def refuse(*args, **kwargs):
        raise InvalidArgument("Say what you want to ask.")
        yield  # pragma: no cover - makes this a generator, like the real stream

    engine.conversations.stream = refuse

    async def scenario(app, pilot):
        await say(app, pilot, "x")
        assert "Say what you want to ask." in transcript(app)

    run(engine, scenario)


def test_the_cli_main_and_import_nl2sql_load_no_textual():
    for statement in ("import nl2sql", "import nl2sql_cli.main"):
        out = subprocess.run([sys.executable, "-c", f"import sys; {statement}; print('textual' in sys.modules)"],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == "False", statement


def test_the_starter_questions_are_asked_for_the_caller():
    engine = FakeEngine([])

    async def scenario(app, pilot):
        await settle(app, pilot)

    run(engine, scenario)
    assert [u.user_id for u in engine.datasource_calls] == ["ana"]


def test_a_result_that_is_gone_is_shown_not_a_crash():
    engine = FakeEngine([turn(ask())])
    engine.page_error = ResultNotFound("The result is gone.")

    async def scenario(app, pilot):
        await say(app, pilot, "customers by country")
        await pilot.press("pagedown")
        await settle(app, pilot)
        assert app.is_running
        assert "Error" in transcript(app)

    run(engine, scenario)


def test_new_is_refused_while_a_turn_runs():
    import threading

    engine = FakeEngine([turn(ask())])
    engine.conversations.gate = gate = threading.Event()

    async def scenario(app, pilot):
        composer = app.query_one("#composer")
        composer.value = "customers by country"
        composer.focus()
        await pilot.press("enter")
        await pilot.pause()
        while not any(c[0] == "stream" for c in engine.conversations.calls):
            await pilot.pause(0.05)
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert "You: customers by country" in transcript(app)  # not cleared
        assert "still running" in transcript(app)
        gate.set()
        await settle(app, pilot)
        assert len(app.query(DataTable)) == 1  # the answer landed in the conversation it was asked in

    run(engine, scenario)
