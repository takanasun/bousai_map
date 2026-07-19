"""データストアの抽象基底。

データ取得元(ローカルファイル / Azure Blob Storage 等)の差異を隠蔽する。
Functions 本体やサービス層はこのインターフェースにのみ依存し、
実体の切り替えはファクトリ (`get_datastore`) と環境変数で行う。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataStoreError(Exception):
    """データストア操作の失敗を表す共通例外。"""


class DataStore(ABC):
    """加工済みオープンデータ(JSON)を読み出すための抽象インターフェース。"""

    @abstractmethod
    def load_json(self, name: str) -> Any:
        """指定名の JSON リソースを読み込んで Python オブジェクトを返す。

        Args:
            name: リソース名(例: "hospitals" または "hospitals.json")。

        Raises:
            DataStoreError: リソースが存在しない、または内容が不正な JSON の場合。
        """
        raise NotImplementedError

    @abstractmethod
    def exists(self, name: str) -> bool:
        """指定名の JSON リソースが存在するか。

        「利用可能なメッシュ解像度の一覧」のように、データが置かれているか
        どうかで機能の有無が変わる場面で使う。例外を投げないこと。
        """
        raise NotImplementedError
