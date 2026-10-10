import json

from app.store import Store


def test_legacy_curtains_collection_loads_as_remotes(tmp_path):
    path = tmp_path / "store.json"
    path.write_text(json.dumps({"curtains": [{"id": "a", "name": "Garage"}]}))
    store = Store(path)
    assert store.list("remotes") == [{"id": "a", "name": "Garage"}]

    store.upsert("codes", {"id": "c", "name": "x"})
    saved = json.loads(path.read_text())
    assert "curtains" not in saved and saved["remotes"][0]["id"] == "a"
