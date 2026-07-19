"""DataStore 抽象化レイヤーのテスト。"""

import json

import pytest

from src.datastore import DataStoreError, get_datastore
from src.datastore.local import LocalFileDataStore


def test_get_datastore_default_is_local():
    store = get_datastore()
    assert isinstance(store, LocalFileDataStore)


def test_local_store_loads_json(tmp_path):
    (tmp_path / "sample.json").write_text(
        json.dumps({"hello": "世界"}), encoding="utf-8"
    )
    store = LocalFileDataStore(str(tmp_path))
    assert store.load_json("sample") == {"hello": "世界"}
    # 拡張子付きでも読める
    assert store.load_json("sample.json") == {"hello": "世界"}


def test_local_store_missing_file_raises(tmp_path):
    store = LocalFileDataStore(str(tmp_path))
    with pytest.raises(DataStoreError):
        store.load_json("nope")


def test_local_store_invalid_json_raises(tmp_path):
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    store = LocalFileDataStore(str(tmp_path))
    with pytest.raises(DataStoreError):
        store.load_json("broken")


def test_blob_backend_not_implemented(monkeypatch):
    monkeypatch.setenv("DATA_STORE_BACKEND", "blob")
    with pytest.raises(DataStoreError):
        get_datastore()
