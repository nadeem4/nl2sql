"""The decisions in ``scripts/record_home_clips.py`` that need no browser.

The script itself boots the demo and drives Chromium; what is tested here is
which clips it records, which it leaves as a "Clip coming" poster, and where
each file goes. It never needs a key.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "record_home_clips.py"
GROUPS = [{"datasource": "chinook", "questions": ["a", "b"]},
          {"datasource": "support", "questions": ["c"]}]


def _load():
    spec = importlib.util.spec_from_file_location("record_home_clips", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ask_is_recorded_only_from_a_real_recorded_answer():
    clips = _load()

    assert clips.plan_clips({"question_groups": GROUPS, "recorded": []}) == {
        "ask": "placeholder", "pipeline": "record", "retrieval": "record"}
    assert clips.plan_clips({"question_groups": GROUPS, "recorded": ["c"]})["ask"] == "record"


def test_the_ask_clip_asks_the_first_recorded_guided_question():
    clips = _load()

    assert clips.sample_question({"question_groups": GROUPS, "recorded": ["c", "b"]}) == "b"
    assert clips.sample_question({"question_groups": GROUPS}) is None


def test_each_feature_writes_a_light_and_a_dark_pair_where_the_page_reads_them():
    clips = _load()

    assert clips.clip_names("ask", "light") == {"video": "ask.webm", "poster": "ask.jpg"}
    assert clips.clip_names("retrieval", "dark") == {"video": "retrieval-dark.webm", "poster": "retrieval-dark.jpg"}
    assert clips.OUT.as_posix().endswith("nl2sql/cli/demo/playground/assets/clips")
    assert clips.SIZE == (1280, 800)


def test_a_placeholder_says_plainly_that_the_clip_is_coming():
    clips = _load()

    for theme in ("light", "dark"):
        html = clips.placeholder_html("ask", theme)
        assert "Clip coming" in html
        assert clips.PALETTE[theme]["paper"] in html


def test_an_unknown_feature_is_refused_before_anything_boots(capsys):
    assert _load().main(["--features", "ask,charts", "--url", "http://127.0.0.1:9/"]) == 2
    assert "charts" in capsys.readouterr().err
