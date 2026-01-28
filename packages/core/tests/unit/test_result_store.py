from nl2sql.common.result_store import ResultStore
from nl2sql_adapter_sdk.contracts import ResultFrame


def test_result_store_hash_is_deterministic_for_same_payload():
    # Validates deterministic ids for identical content because caching relies on it.
    # Arrange
    store = ResultStore()
    frame = ResultFrame.from_row_dicts([{"id": 1, "name": "Ada"}])
    metadata = {"trace_id": "t-1"}

    # Act
    first_id = store.put(frame, metadata=metadata)
    second_id = store.put(frame, metadata=metadata)

    # Assert
    assert first_id == second_id
    assert store.get(first_id).row_count == 1


def test_result_store_changes_id_when_metadata_changes():
    # Validates metadata isolation because trace-specific results must not collide.
    # Arrange
    store = ResultStore()
    frame = ResultFrame.from_row_dicts([{"id": 1, "name": "Ada"}])

    # Act
    first_id = store.put(frame, metadata={"trace_id": "t-1"})
    second_id = store.put(frame, metadata={"trace_id": "t-2"})

    # Assert
    assert first_id != second_id
    assert store.get_metadata(first_id)["trace_id"] == "t-1"
