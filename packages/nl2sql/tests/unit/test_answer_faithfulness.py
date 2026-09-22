"""The written answer's numbers and quoted names must come from the result rows.

Tier 1 and tier 2 score the rows; this checks the sentence the answer
synthesizer writes about them, deterministically, with no model.
"""
import pytest

from nl2sql.evaluation.faithfulness import check_answer, extract_numbers

GENRES = (["Genre", "Tracks"], [["Rock", 1297], ["Latin", 579], ["Metal", 374]])
SALES = (["Country", "Total"], [["USA", 523.06], ["Canada", 303.96], ["France", 195.1]])


def _check(text, table=GENRES, question="Which genres have the most tracks?"):
    columns, rows = table
    return check_answer(text, columns=columns, rows=rows, question=question)


# --- extraction -----------------------------------------------------------------

@pytest.mark.parametrize("text, numbers", [
    ("Rock has 1,297 tracks.", [1297.0]),
    ("Total $1,234.50 and 1234.5", [1234.5, 1234.5]),
    ("It rose 12.5% over 3 years", [12.5, 3.0]),
    ("They were the 2nd and 3rd largest", []),
    ("chinook_001, MPEG-4 and track#7", []),
    ("On 2013-05-01 sales were 4.95.", [2013.0, 4.95]),
    ("1. Rock\n2. Latin\n- 3 genres", [3.0]),
    ("It fell to -5 points", [-5.0]),
])
def test_extraction_reads_integers_decimals_currency_percentages_and_separators(text, numbers):
    assert [n.value for n in extract_numbers(text)] == numbers


def test_extraction_keeps_the_written_precision():
    written = extract_numbers("$1,234.50, 12% and 7")
    assert [(n.value, n.decimals, n.percent) for n in written] == [(1234.5, 2, False), (12.0, 0, True), (7.0, 0, False)]


# --- good answers -----------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Rock leads with 1297 tracks, then Latin (579) and Metal (374).",
    "Rock leads with 1,297 tracks.",
    "The 3 genres with the most tracks are Rock, Latin and Metal.",       # the row count
    "Together they have 2,250 tracks.",                                   # a column sum
    "1. **Rock**: 1297\n2. **Latin**: 579\n3. **Metal**: 374",            # list markers are not numbers
    "Rock is 1st, Latin 2nd and Metal 3rd.",                              # ordinals
    "No numbers here, just Rock.",
])
def test_a_faithful_answer(text):
    verdict = _check(text)
    assert verdict["faithful"] is True, verdict
    assert verdict["unsupported_numbers"] == []


def test_numbers_match_the_rows_within_the_precision_written():
    for text in ("USA spent $523.06.", "USA spent $523.1.", "USA spent about $523.", "France spent $195.10.",
                 "France spent 195.1"):
        assert _check(text, SALES)["faithful"], text


def test_numbers_in_the_question_are_supported():
    verdict = _check("The top 5 genres in 2013 were Rock and Latin.", question="Top 5 genres in 2013?")
    assert verdict["faithful"] and verdict["checked"] >= 2


def test_a_year_in_a_date_cell_is_supported():
    verdict = check_answer("The first invoice was in 2009.", columns=["InvoiceDate"],
                           rows=[["2009-01-01 00:00:00"]], question="When was the first invoice?")
    assert verdict["faithful"]


def test_a_percentage_can_be_a_share_or_a_percent_value():
    shares = (["Genre", "Share"], [["Rock", 0.3718]])
    assert _check("Rock is 37.18% of tracks.", shares)["faithful"]
    assert _check("Rock is 37.2% of tracks.", shares)["faithful"]
    percents = (["Genre", "Pct"], [["Rock", 37.18]])
    assert _check("Rock is 37.18% of tracks.", percents)["faithful"]


# --- bad answers ------------------------------------------------------------------

def test_a_wrong_number_is_unsupported():
    verdict = _check("Rock leads with 1,300 tracks, then Latin (579).")
    assert verdict["faithful"] is False
    assert verdict["unsupported_numbers"] == ["1,300"]
    assert verdict["checked"] == 2


def test_more_precision_than_the_rows_have_is_unsupported():
    # 523.06 written as 523.60 is a transposition, not a rounding.
    assert _check("USA spent $523.60.", SALES)["unsupported_numbers"] == ["523.60"]


def test_a_year_that_is_nowhere_is_unsupported():
    assert _check("Rock peaked in 2013.")["unsupported_numbers"] == ["2013"]


def test_a_quoted_or_bold_name_must_be_in_the_rows():
    verdict = _check('The top genre is "Jazz", then **Latin**.')
    assert verdict["faithful"] is False
    assert verdict["unsupported_entities"] == ["Jazz"]


def test_bold_labels_and_column_names_are_not_entities():
    verdict = _check("**Note:** the **Tracks** column counts tracks. **Rock** leads.")
    assert verdict["faithful"], verdict
    assert verdict["unsupported_entities"] == []


def test_no_answer_text_is_not_checked():
    assert check_answer("", columns=[], rows=[], question="?") == {
        "faithful": True, "unsupported_numbers": [], "unsupported_entities": [], "checked": 0}
