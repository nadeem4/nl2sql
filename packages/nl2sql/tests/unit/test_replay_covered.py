"""ReplayStore.covered: which guided questions a replay can start answering."""

from nl2sql.llm.replay import Recording, ReplayStore


def _decomposer(question):
    return Recording("DecomposerResponse", question, {"sub_queries": []})


def _choice(question, query_id):
    return Recording("VerifiedQueryChoice", question, {"query_id": query_id, "params": []})


def test_a_question_with_a_decomposer_recording_is_covered():
    store = ReplayStore([_decomposer("q1")])
    assert store.covered(["q1", "q2"]) == ["q1"]


def test_a_verified_hit_is_covered_without_a_decomposer_recording():
    # A verified answer never reaches the decomposer, so its run records only the choice.
    store = ReplayStore([_choice("q1", "chinook.revenue_by_year")])
    assert store.covered(["q1"]) == ["q1"]


def test_none_of_these_still_needs_a_decomposer_recording():
    store = ReplayStore([_choice("q1", "none_of_these"), _choice("q2", "none_of_these"), _decomposer("q2")])
    assert store.covered(["q1", "q2"]) == ["q2"]
