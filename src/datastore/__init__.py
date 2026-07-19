"""データストアのファクトリ。

環境変数 `DATA_STORE_BACKEND` に応じて実体を生成する。
これにより「ローカル(モック) → Azure Blob Storage(本番)」の差し替えを
呼び出し側のコード変更なしで行える。
"""

from __future__ import annotations

import logging

from .. import config
from .base import DataStore, DataStoreError
from .local import LocalFileDataStore

logger = logging.getLogger(__name__)

__all__ = ["DataStore", "DataStoreError", "LocalFileDataStore", "get_datastore"]


def get_datastore() -> DataStore:
    """環境変数に基づき DataStore 実装を返すファクトリ。"""
    backend = config.data_store_backend()

    if backend == "local":
        return LocalFileDataStore(config.local_data_dir())

    if backend == "blob":
        # 将来 Azure 環境が整った際にここへ BlobDataStore を実装する。
        # 例: from .blob import BlobDataStore
        #     return BlobDataStore(config.blob_connection_string(),
        #                          config.blob_container_name())
        raise DataStoreError(
            "blob backend is not implemented yet; set DATA_STORE_BACKEND=local"
        )

    raise DataStoreError(f"unknown DATA_STORE_BACKEND: {backend!r}")
