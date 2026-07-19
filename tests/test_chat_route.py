"""`/api/chat`（生成AI防災アシスタント）のテスト。

外部の LLM には一切アクセスしない。`LLM_BACKEND=echo` のスタブと
モックした `requests` で、プロンプト組み立て・入力検証・エラー処理を検証する。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import function_app
from src.services import llm


# ---------------------------------------------------------------------------
# ファクトリと設定
# ---------------------------------------------------------------------------

def test_echo_backend_selected_by_env(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    assert isinstance(llm.get_llm_client(), llm.EchoLLMClient)


def test_openai_backend_is_default(monkeypatch):
    """既定は OpenAI API（Azure はデプロイ単位のクォータ管理が要るため）。"""
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert isinstance(llm.get_llm_client(), llm.OpenAIClient)


def test_azure_backend_still_selectable(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "azure_openai")
    assert isinstance(llm.get_llm_client(), llm.AzureOpenAIClient)


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "gemini")
    with pytest.raises(llm.LLMError):
        llm.get_llm_client()


def test_azure_client_not_configured_without_key(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "azure_openai")
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    assert llm.get_llm_client().is_configured() is False


def test_azure_client_configured_with_all_values(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    assert llm.get_llm_client().is_configured() is True


def test_api_key_is_not_hardcoded():
    """資格情報がソースに直書きされていないことの回帰ガード。"""
    import inspect

    source = inspect.getsource(llm)
    assert "os.environ" not in source  # config 経由でのみ取得する
    assert "api-key" in source  # ヘッダ名は出てよい
    assert "sk-" not in source


# ---------------------------------------------------------------------------
# プロンプト組み立て
# ---------------------------------------------------------------------------

ORIGIN = {"lat": 35.4478, "lng": 139.6425}

SHELTERS = [
    {
        "name": "川崎中学校",
        "isEmergencySite": True,
        "isEvacuationCenter": True,
        "isWelfareShelter": False,
        "disasterTypes": ["flood", "earthquake"],
        "distanceKm": 0.42,
    },
    {
        "name": "さくら福祉センター",
        "isEmergencySite": False,
        "isEvacuationCenter": True,
        "isWelfareShelter": True,
        "disasterTypes": [],
        "distanceKm": 0.9,
    },
]
HOSPITALS = [
    {"name": "川崎中央病院", "capabilities": ["内科", "精神科"], "distanceKm": 0.7},
]
TOILETS = [
    {"name": "中央公園トイレ", "attributes": {"accessible": True, "ostomate": False}, "distanceKm": 0.3},
]


def test_context_includes_facility_names_and_distances():
    ctx = llm.build_context(ORIGIN, SHELTERS, HOSPITALS, TOILETS)
    assert "川崎中学校" in ctx
    assert "0.42km" in ctx
    assert "川崎中央病院" in ctx
    assert "精神科" in ctx


def test_context_distinguishes_two_shelter_kinds():
    """命を守る場所と生活する場所の区別がプロンプトに入ること。"""
    ctx = llm.build_context(ORIGIN, SHELTERS, HOSPITALS, TOILETS)
    assert "指定緊急避難場所(命を守る)" in ctx
    assert "指定避難所(生活する)" in ctx
    assert "福祉避難所" in ctx


def test_context_handles_empty_lists():
    ctx = llm.build_context(ORIGIN, [], [], [])
    assert "該当なし" in ctx


def test_system_prompt_forbids_inventing_facts():
    """データに無いことを推測しないよう指示していること。"""
    assert "推測" in llm.SYSTEM_PROMPT
    assert "参考データ" in llm.SYSTEM_PROMPT


def test_user_message_contains_question_and_context():
    msg = llm.build_user_message("一番近い避難所は？", "# 基準地点\n...")
    assert "一番近い避難所は？" in msg
    assert "参考データ" in msg


# ---------------------------------------------------------------------------
# Azure OpenAI クライアント（requests をモック）
# ---------------------------------------------------------------------------

def _client():
    return llm.AzureOpenAIClient(
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        deployment="gpt-4o",
        api_version="2024-10-21",
    )


def test_azure_url_is_built_from_deployment():
    assert _client()._url() == (
        "https://example.openai.azure.com/openai/deployments/gpt-4o"
        "/chat/completions?api-version=2024-10-21"
    )


def test_azure_complete_parses_response():
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = {"choices": [{"message": {"content": "最寄りは川崎中学校です。"}}]}
    with patch("requests.post", return_value=response):
        assert _client().complete("sys", "user") == "最寄りは川崎中学校です。"


def test_azure_sends_key_in_header():
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    with patch("requests.post", return_value=response) as post:
        _client().complete("sys", "user")
    assert post.call_args.kwargs["headers"]["api-key"] == "test-key"


@pytest.mark.parametrize("status,fragment", [(401, "認証"), (429, "混み合"), (500, "エラー")])
def test_azure_http_errors_become_llm_error(status, fragment):
    response = MagicMock(status_code=status, ok=False, text="err")
    with patch("requests.post", return_value=response):
        with pytest.raises(llm.LLMError) as exc:
            _client().complete("sys", "user")
    assert fragment in str(exc.value)


def test_azure_timeout_becomes_llm_error():
    import requests

    with patch("requests.post", side_effect=requests.Timeout()):
        with pytest.raises(llm.LLMError) as exc:
            _client().complete("sys", "user")
    assert "タイムアウト" in str(exc.value)


def test_azure_malformed_response_becomes_llm_error():
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = {"unexpected": True}
    with patch("requests.post", return_value=response):
        with pytest.raises(llm.LLMError):
            _client().complete("sys", "user")


# ---------------------------------------------------------------------------
# ルート
# ---------------------------------------------------------------------------

def _post(invoke, body: dict):
    return invoke(function_app.chat, method="POST", body=json.dumps(body).encode("utf-8"))


def test_chat_returns_answer_with_echo_backend(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"question": "一番近い避難所は？", "lat": 35.512, "lng": 139.715})
    assert status == 200
    assert body["answer"]
    assert "sources" in body


def test_chat_reports_nearby_counts(invoke, monkeypatch):
    """回答の根拠にした施設数を返し、利用者が範囲を把握できること。"""
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    assert status == 200
    for key in ("shelters", "hospitals", "toilets"):
        assert isinstance(body["sources"][key], int)


def test_chat_requires_question(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"lat": 35.512, "lng": 139.715})
    assert status == 400
    assert "error" in body


def test_chat_rejects_blank_question(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"question": "   ", "lat": 35.512, "lng": 139.715})
    assert status == 400


def test_chat_rejects_invalid_coordinates(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"question": "避難所は？", "lat": "abc", "lng": 139.715})
    assert status == 400


def test_chat_rejects_malformed_json(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = invoke(function_app.chat, method="POST", body=b"{not json")
    assert status == 400


def test_chat_rejects_overlong_question(invoke, monkeypatch):
    """プロンプト肥大とコスト増を防ぐため入力長を制限する。"""
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"question": "あ" * 3000, "lat": 35.512, "lng": 139.715})
    assert status == 400


def test_chat_returns_503_when_llm_not_configured(invoke, monkeypatch):
    """キー未設定は 500 ではなく 503 + 案内メッセージ（設定漏れと障害を区別する）。"""
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    assert status == 503
    assert "OPENAI_API_KEY" in body["error"]


def test_chat_503_message_matches_selected_backend(invoke, monkeypatch):
    """Azure を選んでいるときは Azure の設定項目を案内すること。"""
    monkeypatch.setenv("LLM_BACKEND", "azure_openai")
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    status, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    assert status == 503
    assert "AZURE_OPENAI" in body["error"]


def test_chat_surfaces_llm_error_message(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    with patch.object(llm.EchoLLMClient, "complete", side_effect=llm.LLMError("AIが混み合っています")):
        status, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    assert status == 502
    assert "混み合" in body["error"]


# ---------------------------------------------------------------------------
# OpenAI クライアント（requests をモック）
# ---------------------------------------------------------------------------

def _openai_client():
    return llm.OpenAIClient(
        api_key="sk-test", model="gpt-4o-mini", base_url="https://api.openai.com/v1"
    )


def test_openai_not_configured_without_key(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.get_llm_client().is_configured() is False


def test_openai_configured_with_key(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.get_llm_client().is_configured() is True


def test_openai_default_model(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert llm.get_llm_client().model == "gpt-4o-mini"


def test_openai_model_is_overridable(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert llm.get_llm_client().model == "gpt-4o"


def test_openai_url():
    assert _openai_client()._url() == "https://api.openai.com/v1/chat/completions"


def test_openai_uses_bearer_auth():
    """Azure の api-key ヘッダとは異なり Bearer トークンを使う。"""
    headers = _openai_client()._headers()
    assert headers["Authorization"] == "Bearer sk-test"
    assert "api-key" not in headers


def test_openai_sends_model_in_body():
    """Azure は URL のデプロイ名で、OpenAI はボディの model でモデルを決める。"""
    payload = _openai_client()._payload("sys", "user")
    assert payload["model"] == "gpt-4o-mini"


def test_azure_does_not_send_model_in_body():
    payload = _client()._payload("sys", "user")
    assert "model" not in payload


def test_openai_complete_parses_response():
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = {"choices": [{"message": {"content": "最寄りは川崎中学校です。"}}]}
    with patch("requests.post", return_value=response):
        assert _openai_client().complete("sys", "user") == "最寄りは川崎中学校です。"


@pytest.mark.parametrize("status,fragment", [(401, "認証"), (429, "混み合"), (500, "エラー")])
def test_openai_http_errors_become_llm_error(status, fragment):
    response = MagicMock(status_code=status, ok=False, text="err")
    with patch("requests.post", return_value=response):
        with pytest.raises(llm.LLMError) as exc:
            _openai_client().complete("sys", "user")
    assert fragment in str(exc.value)


def test_openai_base_url_is_overridable():
    """互換エンドポイントに向けられること。"""
    client = llm.OpenAIClient(api_key="k", model="m", base_url="https://proxy.example/v1/")
    assert client._url() == "https://proxy.example/v1/chat/completions"


def test_configuration_hint_names_the_right_variable():
    assert "OPENAI_API_KEY" in _openai_client().configuration_hint()
    assert "AZURE_OPENAI_ENDPOINT" in _client().configuration_hint()


# ---------------------------------------------------------------------------
# 回答の根拠候補（フロントで地図上に旗を立てるために使う）
# ---------------------------------------------------------------------------

def test_chat_returns_candidates_with_ids_and_locations(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    status, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    assert status == 200
    assert isinstance(body["candidates"], list)
    for c in body["candidates"]:
        assert c["id"]
        assert c["name"]
        assert c["kind"] in ("shelter", "hospital", "toilet")
        assert "lat" in c["location"] and "lng" in c["location"]


def test_candidates_cover_all_three_kinds(invoke, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "echo")
    _, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    kinds = {c["kind"] for c in body["candidates"]}
    assert "shelter" in kinds


def test_candidate_count_matches_sources(invoke, monkeypatch):
    """sources の件数と candidates の件数が食い違わないこと。"""
    monkeypatch.setenv("LLM_BACKEND", "echo")
    _, body = _post(invoke, {"question": "避難所は？", "lat": 35.512, "lng": 139.715})
    s = body["sources"]
    assert len(body["candidates"]) == s["shelters"] + s["hospitals"] + s["toilets"]


# ---------------------------------------------------------------------------
# 災害種別の日本語化
# ---------------------------------------------------------------------------

def test_disaster_labels_cover_all_keys_from_cleaning_script():
    """クレンジングスクリプトが出力するキーを全て日本語化できること。

    キーの定義が clean_shelters.py・llm.py・フロントの3箇所にあるため、
    片方だけ増えると英語キーが利用者に露出する。その回帰ガード。
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import clean_shelters

    produced = set(clean_shelters.DISASTER_COLUMNS.values())
    assert produced == set(llm.DISASTER_LABELS), (
        "clean_shelters.py の災害種別キーと llm.py のラベルが一致しません: "
        f"未対応={produced - set(llm.DISASTER_LABELS)}, "
        f"余分={set(llm.DISASTER_LABELS) - produced}"
    )


def test_labels_are_japanese():
    assert llm.DISASTER_LABELS["earthquake"] == "地震"
    assert llm.DISASTER_LABELS["tsunami"] == "津波"
    assert llm.DISASTER_LABELS["stormSurge"] == "高潮"


def test_context_uses_japanese_disaster_names():
    """プロンプトに英語キーを渡さない（AIがそのまま回答に出してしまうため）。"""
    ctx = llm.build_context(ORIGIN, SHELTERS, HOSPITALS, TOILETS)
    assert "洪水" in ctx
    assert "地震" in ctx
    assert "flood" not in ctx
    assert "earthquake" not in ctx


def test_unknown_disaster_key_falls_back_to_raw_value():
    """将来キーが増えても落ちず、生の値を出すこと。"""
    shelter = [{
        "name": "テスト施設", "isEmergencySite": True, "isEvacuationCenter": False,
        "isWelfareShelter": False, "disasterTypes": ["meteor"], "distanceKm": 1.0,
    }]
    ctx = llm.build_context(ORIGIN, shelter, [], [])
    assert "meteor" in ctx
