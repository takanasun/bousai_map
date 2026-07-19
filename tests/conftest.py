"""pytest 共通フィクスチャ / ヘルパ。

Azure Functions V2 の `@app.route` デコレータはユーザ関数を FunctionBuilder
でラップするため、テストから直接呼ぶには元の関数を取り出す必要がある。
`invoke_route` はその差異を吸収し、HttpRequest を渡してレスポンスを得る。

またテスト実行中は `LOCAL_DATA_DIR` を `tests/fixtures` に固定する。
`data/processed/` には `scripts/` 配下のスクリプトが取得した実データ
(神奈川県全域の数千件)が入るため、テストがそれに依存すると
データ更新のたびに壊れてしまう。テストは常に固定のモックを見る。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import azure.functions as func
import pytest

# テスト用の固定データセット置き場 (このファイルは <root>/tests/conftest.py)
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(autouse=True)
def use_fixture_data(monkeypatch):
    """全テストのデータ読み込み先を `tests/fixtures` に向ける。

    `config.local_data_dir()` は呼び出しのたびに環境変数を読むため、
    環境変数の差し替えだけで DataStore の参照先を切り替えられる。
    `monkeypatch` がテスト終了時に元の値へ復元する。
    """
    monkeypatch.setenv("LOCAL_DATA_DIR", FIXTURES_DIR)
    monkeypatch.setenv("DATA_STORE_BACKEND", "local")
    monkeypatch.setenv("GEOCODER_BACKEND", "mock")


@pytest.fixture
def fixtures_dir() -> str:
    """テスト用データセットのディレクトリパス。"""
    return FIXTURES_DIR


@pytest.fixture
def load_fixture():
    """`load_fixture("toilets")` でテスト用データセットを読み込むヘルパ。

    期待値をテスト内にハードコードせず、フィクスチャファイルから導出したい
    場合に使う。
    """

    def _load(name: str) -> Any:
        filename = name if name.endswith(".json") else f"{name}.json"
        with open(os.path.join(FIXTURES_DIR, filename), "r", encoding="utf-8") as fp:
            return json.load(fp)

    return _load


def _unwrap(handler: Any):
    """`@app.route` が返すオブジェクトから呼び出し可能なユーザ関数を取り出す。"""
    # azure-functions のバージョン差異に備え、複数経路をフォールバックで試す。
    if hasattr(handler, "get_user_function"):
        return handler.get_user_function()
    inner = getattr(handler, "_function", None)
    if inner is not None and hasattr(inner, "get_user_function"):
        return inner.get_user_function()
    return handler  # 素の関数ならそのまま


def make_request(
    params: Optional[Dict[str, str]] = None,
    method: str = "GET",
    route: str = "test",
    body: bytes = b"",
) -> func.HttpRequest:
    """テスト用の HttpRequest を生成する。"""
    return func.HttpRequest(
        method=method,
        url=f"/api/{route}",
        params=params or {},
        body=body,
    )


def invoke_route(
    handler: Any,
    params: Optional[Dict[str, str]] = None,
    method: str = "GET",
    body: bytes = b"",
):
    """ルートハンドラを呼び出し、(status_code, parsed_json) を返す。"""
    fn = _unwrap(handler)
    req = make_request(params=params, method=method, body=body)
    resp = fn(req)
    try:
        body = json.loads(resp.get_body().decode("utf-8"))
    except (ValueError, AttributeError):
        body = None
    return resp.status_code, body


@pytest.fixture
def invoke():
    """`invoke(handler, params=...)` の形で使えるフィクスチャ。"""
    return invoke_route
