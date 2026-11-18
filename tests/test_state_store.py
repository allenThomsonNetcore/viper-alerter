"""State store behavior tests."""
from checker.state_store import FileStateStore


def test_file_store_roundtrip(tmp_path):
    """Local JSON state persists records."""
    store = FileStateStore(str(tmp_path / "state.json"))
    store.save({"a": {"state": "failed"}})
    assert store.load()["a"]["state"] == "failed"
