"""`/api/config` ルートと Azure Maps キーの取り扱いのテスト。

Azure Maps のキーはフロントエンドの JS にハードコードせず、Functions 経由で
配布する。キー未設定でも 200 を返し、フロント側が案内を表示できるようにする。
"""

from __future__ import annotations

import inspect
import os

import function_app
from src import config


# --- 設定読み込み ----------------------------------------------------------- #

def test_subscription_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "test-primary-key")
    assert config.azure_maps_subscription_key() == "test-primary-key"


def test_subscription_key_falls_back_to_legacy_name(monkeypatch):
    monkeypatch.delenv("AZURE_MAPS_SUBSCRIPTION_KEY", raising=False)
    monkeypatch.setenv("AZURE_MAPS_KEY", "legacy-key")
    assert config.azure_maps_subscription_key() == "legacy-key"


def test_subscription_key_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("AZURE_MAPS_SUBSCRIPTION_KEY", raising=False)
    monkeypatch.delenv("AZURE_MAPS_KEY", raising=False)
    assert config.azure_maps_subscription_key() == ""


def test_key_is_not_hardcoded_in_source():
    """資格情報がソースに直書きされていないことの回帰ガード。"""
    source = inspect.getsource(config)
    # os.environ 経由でのみ取得していること
    assert "os.environ" in source
    # それらしいキー文字列がリテラルとして埋まっていないこと
    assert "AZURE_MAPS_SUBSCRIPTION_KEY=" not in source.replace(" ", "")


def test_dotenv_does_not_override_existing_env(monkeypatch, tmp_path):
    """既存の環境変数を .env が上書きしないこと（local.settings.json 優先）。"""
    dotenv = tmp_path / ".env"
    dotenv.write_text('AZURE_MAPS_SUBSCRIPTION_KEY="from-dotenv"\n', encoding="utf-8")

    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "from-environment")
    config.load_dotenv(str(dotenv))
    assert config.azure_maps_subscription_key() == "from-environment"


def test_dotenv_sets_unset_variables_and_strips_quotes(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# コメント行\n"
        "\n"
        'AZURE_MAPS_SUBSCRIPTION_KEY="quoted-key"\n',
        encoding="utf-8",
    )

    monkeypatch.delenv("AZURE_MAPS_SUBSCRIPTION_KEY", raising=False)
    config.load_dotenv(str(dotenv))
    assert os.environ["AZURE_MAPS_SUBSCRIPTION_KEY"] == "quoted-key"


def test_dotenv_missing_file_is_ignored(tmp_path):
    """`.env` が無い環境でも例外にならないこと。"""
    config.load_dotenv(str(tmp_path / "does_not_exist.env"))


# --- ルート ----------------------------------------------------------------- #

def test_config_route_returns_key(invoke, monkeypatch):
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "test-primary-key")
    status, body = invoke(function_app.map_config)
    assert status == 200
    assert body["azureMapsKey"] == "test-primary-key"
    assert body["configured"] is True


def test_config_route_without_key_still_returns_200(invoke, monkeypatch):
    """キー未設定でも 500 にせず、フロントが案内を出せる形で返す。"""
    monkeypatch.delenv("AZURE_MAPS_SUBSCRIPTION_KEY", raising=False)
    monkeypatch.delenv("AZURE_MAPS_KEY", raising=False)
    status, body = invoke(function_app.map_config)
    assert status == 200
    assert body["azureMapsKey"] == ""
    assert body["configured"] is False
    assert "message" in body


# --- AIアシスタントの設定状態（フロントの案内文もここから配る） ------------- #

def test_config_reports_chat_not_configured(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status, body = invoke(function_app.map_config)
    assert status == 200
    assert body["chatConfigured"] is False


def test_config_hint_matches_selected_backend(invoke, monkeypatch):
    """案内文をフロントにハードコードすると、バックエンドを替えた時に嘘になる。

    サーバが選択中のバックエンドに応じた文言を返し、フロントはそれを表示するだけにする。
    """
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, body = invoke(function_app.map_config)
    assert "OPENAI_API_KEY" in body["chatConfigHint"]
    assert "AZURE_OPENAI" not in body["chatConfigHint"]

    monkeypatch.setenv("LLM_BACKEND", "azure_openai")
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    _, body = invoke(function_app.map_config)
    assert "AZURE_OPENAI_ENDPOINT" in body["chatConfigHint"]


def test_config_hint_is_empty_when_configured(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _, body = invoke(function_app.map_config)
    assert body["chatConfigured"] is True
    assert body["chatConfigHint"] == ""


def test_config_survives_unknown_llm_backend(invoke, monkeypatch):
    """LLM_BACKEND のtypoで地図まで落とさないこと。"""
    monkeypatch.setenv("LLM_BACKEND", "gemini")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "test-key")
    status, body = invoke(function_app.map_config)
    assert status == 200
    assert body["configured"] is True   # 地図は使える
    assert body["chatConfigured"] is False


def test_local_settings_does_not_duplicate_dotenv_keys():
    """アプリ設定を local.settings.json と .env の両方に書かないこと。

    `.env` は既存の環境変数を上書きしない設計のため、両方にあると
    func 起動時は local.settings.json が勝ち、.env の編集が無視される。
    実際にジオコーダが mock のまま動く事故が起きたための回帰ガード。
    """
    import json as _json

    path = os.path.join(config.PROJECT_ROOT, "local.settings.json")
    if not os.path.exists(path):
        return  # 配布物には含まれない（.gitignore 済み）

    with open(path, encoding="utf-8") as fp:
        values = _json.load(fp).get("Values", {})

    # Functions ランタイム固有の設定だけを置く
    allowed = {"AzureWebJobsStorage", "FUNCTIONS_WORKER_RUNTIME"}
    duplicated = {k for k in values if not k.startswith("//") and k not in allowed}
    assert not duplicated, (
        f"local.settings.json にアプリ設定が残っています: {sorted(duplicated)}。"
        " これらは .env に一本化してください。"
    )
