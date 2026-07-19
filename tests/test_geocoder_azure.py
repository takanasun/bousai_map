"""Azure Maps Search API を使ったジオコーダのテスト。

外部APIには一切アクセスしない（`requests` をモックする）。
住所入力欄はこのジオコーダが動かないと機能しないため、
応答パース・エラー処理・キー未設定を重点的に検証する。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services import geocoding


def _client():
    return geocoding.AzureMapsGeocoder(subscription_key="test-key")


def _ok(results):
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = {"results": results}
    return response


# --- ファクトリ -------------------------------------------------------------- #

def test_azure_maps_backend_selectable(monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "azure_maps")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "k")
    assert isinstance(geocoding.get_geocoder(), geocoding.AzureMapsGeocoder)


def test_mock_backend_still_selectable(monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "mock")
    assert isinstance(geocoding.get_geocoder(), geocoding.MockGeocoder)


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "google")
    with pytest.raises(ValueError):
        geocoding.get_geocoder()


# --- 応答のパース ------------------------------------------------------------ #

def test_geocode_parses_position():
    response = _ok([{"position": {"lat": 35.4478, "lon": 139.6425}}])
    with patch("requests.get", return_value=response):
        assert _client().geocode("横浜市中区日本大通1") == {"lat": 35.4478, "lng": 139.6425}


def test_geocode_uses_first_result():
    response = _ok([
        {"position": {"lat": 35.1, "lon": 139.1}},
        {"position": {"lat": 36.9, "lon": 140.9}},
    ])
    with patch("requests.get", return_value=response):
        assert _client().geocode("川崎")["lat"] == 35.1


def test_geocode_returns_none_when_no_result():
    with patch("requests.get", return_value=_ok([])):
        assert _client().geocode("存在しない住所XYZ") is None


def test_geocode_returns_none_for_blank_query():
    """空文字で外部APIを叩かないこと（無駄な課金を避ける）。"""
    with patch("requests.get") as get:
        assert _client().geocode("   ") is None
    get.assert_not_called()


def test_geocode_returns_none_when_position_malformed():
    with patch("requests.get", return_value=_ok([{"position": {}}])):
        assert _client().geocode("川崎") is None


# --- リクエストの組み立て ---------------------------------------------------- #

def test_request_scopes_to_japan_and_japanese():
    """国内に限定し日本語で返させる（誤ヒットを減らす）。"""
    with patch("requests.get", return_value=_ok([{"position": {"lat": 1, "lon": 2}}])) as get:
        _client().geocode("川崎")
    params = get.call_args.kwargs["params"]
    assert params["countrySet"] == "JP"
    assert params["language"] == "ja-JP"
    assert params["query"] == "川崎"


def test_subscription_key_is_sent_as_param():
    with patch("requests.get", return_value=_ok([{"position": {"lat": 1, "lon": 2}}])) as get:
        _client().geocode("川崎")
    assert get.call_args.kwargs["params"]["subscription-key"] == "test-key"


def test_request_has_timeout():
    """外部API呼び出しにタイムアウトを設定していること。"""
    with patch("requests.get", return_value=_ok([{"position": {"lat": 1, "lon": 2}}])) as get:
        _client().geocode("川崎")
    assert get.call_args.kwargs["timeout"] > 0


# --- エラー処理 -------------------------------------------------------------- #

def test_missing_key_raises_on_use():
    client = geocoding.AzureMapsGeocoder(subscription_key="")
    with pytest.raises(geocoding.GeocodingError):
        client.geocode("川崎")


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_become_geocoding_error(status):
    response = MagicMock(status_code=status, ok=False, text="err")
    with patch("requests.get", return_value=response):
        with pytest.raises(geocoding.GeocodingError):
            _client().geocode("川崎")


def test_timeout_becomes_geocoding_error():
    import requests

    with patch("requests.get", side_effect=requests.Timeout()):
        with pytest.raises(geocoding.GeocodingError):
            _client().geocode("川崎")


def test_malformed_json_becomes_geocoding_error():
    response = MagicMock(status_code=200, ok=True)
    response.json.side_effect = ValueError("bad json")
    with patch("requests.get", return_value=response):
        with pytest.raises(geocoding.GeocodingError):
            _client().geocode("川崎")


# --- ルート ------------------------------------------------------------------ #

def test_geocode_route_returns_502_on_geocoding_error(invoke, monkeypatch):
    """外部API障害で画面を白くせず、利用者向けメッセージを返す。"""
    import function_app

    monkeypatch.setenv("GEOCODER_BACKEND", "azure_maps")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "k")
    with patch("requests.get", side_effect=Exception("boom")):
        status, body = invoke(function_app.geocode, params={"address": "川崎"})
    assert status in (500, 502)
    assert "error" in body
