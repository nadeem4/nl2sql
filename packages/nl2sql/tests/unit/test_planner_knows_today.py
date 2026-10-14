"""The planner and refiner are told today's date.

Without it, "this year" or "the last 30 days" was resolved against whatever
year the model guessed, and the answer came back confident and wrong.
``settings.pipeline.today`` pins the date (tests, gold runs); unset, it is the
real date in UTC. The date goes in the per-call human message, never the system
message, so the cached schema prefix stays byte-identical from day to day.
"""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from nl2sql.common.dates import run_date
from nl2sql.config import Settings
from nl2sql.llm.replay import extract_question

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "measure_prompt_tokens.py"
Q = "How many invoices were issued this year?"


@pytest.fixture(scope="module")
def measure():
    spec = importlib.util.spec_from_file_location("measure_prompt_tokens", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_settings_pipeline_today_is_read_as_an_iso_date():
    assert Settings.model_validate({"pipeline": {"today": "2013-12-31"}}).pipeline.today == dt.date(2013, 12, 31)


def test_unset_the_run_date_is_todays_date_in_utc():
    assert run_date(None) == dt.datetime.now(dt.timezone.utc).date()


def test_a_pinned_date_wins():
    assert run_date(dt.date(2013, 12, 31)) == dt.date(2013, 12, 31)


def test_the_planner_gets_the_date_in_the_human_message_only(measure):
    tables = measure.chinook_tables()
    first, _ = measure.render_planner(Q, tables, today=dt.date(2013, 12, 31))
    second, _ = measure.render_planner(Q, tables, today=dt.date(2014, 1, 1))

    assert "2013-12-31" in first[1].content and "2013-12-31" not in first[0].content
    assert "2014-01-01" in second[1].content
    assert first[0].content == second[0].content  # the cached prefix does not move with the date


def test_the_refiner_gets_the_date_in_the_human_message_only(measure):
    tables = measure.chinook_tables()
    first, _ = measure.render_refiner(Q, tables, today=dt.date(2013, 12, 31))
    second, _ = measure.render_refiner(Q, tables, today=dt.date(2014, 1, 1))

    assert "2013-12-31" in first[1].content and "2013-12-31" not in first[0].content
    assert first[0].content == second[0].content


def test_a_trace_replay_is_told_the_date_the_run_was_told():
    """A trace replay compares prompts exactly; a trace recorded yesterday
    would otherwise diverge at the first planner call."""
    from nl2sql.tracing.replay import recorded_run_date

    assert recorded_run_date({"request": {"today": "2013-12-31"}}) == dt.date(2013, 12, 31)
    assert recorded_run_date({"request": {"question": "q"}}) is None  # a trace from before the date
    assert recorded_run_date({}) is None


def test_the_replay_key_is_still_the_question(measure):
    """Demo recordings are keyed by the question the prompt carries."""
    tables = measure.chinook_tables()
    planner, _ = measure.render_planner(Q, tables, today=dt.date(2013, 12, 31))
    refiner, _ = measure.render_refiner(Q, tables, today=dt.date(2013, 12, 31))

    assert extract_question("\n".join(m.content for m in planner)) == Q
    assert extract_question("\n".join(m.content for m in refiner)) == f'"{Q}"'
