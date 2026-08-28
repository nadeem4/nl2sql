from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode


def test_stable_id_is_deterministic_for_same_payload():
    # Validates deterministic IDs because execution graphs must be reproducible.
    # Arrange
    node = DecomposerNode.__new__(DecomposerNode)
    payload = {"datasource_id": "ds1", "intent": "list users", "filters": []}

    # Act
    first = node._stable_id("sq", payload)
    second = node._stable_id("sq", payload)

    # Assert
    assert first == second


def test_stable_id_changes_when_payload_changes():
    # Validates uniqueness because different subqueries must not collide.
    # Arrange
    node = DecomposerNode.__new__(DecomposerNode)
    base = {"datasource_id": "ds1", "intent": "list users", "filters": []}
    changed = {"datasource_id": "ds1", "intent": "list orders", "filters": []}

    # Act
    first = node._stable_id("sq", base)
    second = node._stable_id("sq", changed)

    # Assert
    assert first != second
