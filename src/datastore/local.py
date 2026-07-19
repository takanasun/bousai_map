"""ローカルファイルシステムを用いた DataStore 実装。

Azure Blob Storage の代替として、ローカルの `data/` ディレクトリ内の
JSON ファイルを読み出す。Azure アカウント無しで完全にローカル完結する。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .base import DataStore, DataStoreError

logger = logging.getLogger(__name__)


class LocalFileDataStore(DataStore):
    """`data_dir` 配下の JSON ファイルを読み出す DataStore。"""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir

    def _resolve_path(self, name: str) -> str:
        filename = name if name.endswith(".json") else f"{name}.json"
        return os.path.join(self.data_dir, filename)

    def exists(self, name: str) -> bool:
        return os.path.isfile(self._resolve_path(name))

    def load_json(self, name: str) -> Any:
        path = self._resolve_path(name)
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except FileNotFoundError as exc:
            logger.error("データファイルが見つかりません: %s", path)
            raise DataStoreError(f"data resource not found: {name}") from exc
        except json.JSONDecodeError as exc:
            logger.error("JSON の解析に失敗しました: %s (%s)", path, exc)
            raise DataStoreError(f"invalid JSON in data resource: {name}") from exc
        except OSError as exc:  # 権限エラー等
            logger.error("データファイルの読み込みに失敗しました: %s (%s)", path, exc)
            raise DataStoreError(f"failed to read data resource: {name}") from exc
