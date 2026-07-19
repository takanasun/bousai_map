"""国土地理院ジオコーダのテスト。

外部APIには一切アクセスしない（`requests` をモックする）。

このAPI固有の注意点:
  * 応答は GeoJSON 形式で、座標が [経度, 緯度] の順（緯度経度の逆）
  * 該当なしは空配列（HTTPエラーではない）
  * キー不要
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services import geocoding


def _client():
    return geocoding.GsiGeocoder()


def _ok(items):
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = items
    return response


def _hit(lng, lat, title="神奈川県横浜市中区日本大通１番地"):
    return {"geometry": {"coordinates": [lng, lat], "type": "Point"},
            "properties": {"title": title, "addressCode": "14130"}}


# --- ファクトリ -------------------------------------------------------------- #

def test_gsi_is_default_backend(monkeypatch):
    """既定は国土地理院。Azure Maps は日本の住所をほぼ解決できなかったため。"""
    monkeypatch.delenv("GEOCODER_BACKEND", raising=False)
    assert isinstance(geocoding.get_geocoder(), geocoding.GsiGeocoder)


def test_gsi_backend_selectable(monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "gsi")
    assert isinstance(geocoding.get_geocoder(), geocoding.GsiGeocoder)


def test_other_backends_still_selectable(monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "mock")
    assert isinstance(geocoding.get_geocoder(), geocoding.MockGeocoder)
    monkeypatch.setenv("GEOCODER_BACKEND", "azure_maps")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "k")
    assert isinstance(geocoding.get_geocoder(), geocoding.AzureMapsGeocoder)


def test_gsi_needs_no_credentials(monkeypatch):
    """キー不要であること（未設定でも動く）。"""
    monkeypatch.setenv("GEOCODER_BACKEND", "gsi")
    monkeypatch.delenv("AZURE_MAPS_SUBSCRIPTION_KEY", raising=False)
    with patch("requests.get", return_value=_ok([_hit(139.72, 35.52)])):
        assert geocoding.get_geocoder().geocode("横浜市中区日本大通1") is not None


# --- 応答のパース ------------------------------------------------------------ #

def test_coordinates_are_lng_lat_order():
    """GeoJSON は [経度, 緯度] の順。取り違えると日本から飛び出す。"""
    with patch("requests.get", return_value=_ok([_hit(139.6425, 35.4478)])):
        point = _client().geocode("横浜市中区日本大通1")
    # 対応エリア判定用に prefecture / title も返すため、座標だけを比較する
    assert point["lat"] == 35.4478
    assert point["lng"] == 139.6425
    # 取り違えていれば緯度が139になり日本国外になる
    assert 20.0 <= point["lat"] <= 46.0
    assert 122.0 <= point["lng"] <= 154.0


def test_uses_first_result():
    with patch("requests.get", return_value=_ok([_hit(139.1, 35.1), _hit(140.9, 36.9)])):
        assert _client().geocode("川崎")["lat"] == 35.1


def test_returns_none_when_no_result():
    """該当なしは空配列で返る（HTTPエラーではない）。"""
    with patch("requests.get", return_value=_ok([])):
        assert _client().geocode("存在しない住所XYZ") is None


def test_returns_none_for_blank_query():
    with patch("requests.get") as get:
        assert _client().geocode("   ") is None
    get.assert_not_called()


def test_returns_none_when_geometry_malformed():
    broken = {"geometry": {"coordinates": []}, "properties": {}}
    with patch("requests.get", return_value=_ok([broken])):
        assert _client().geocode("川崎") is None


def test_rejects_coordinates_outside_japan():
    """明らかに国外の座標は誤りとして採用しない。"""
    with patch("requests.get", return_value=_ok([_hit(-74.0, 40.7)])):  # ニューヨーク
        assert _client().geocode("川崎") is None


# --- リクエスト -------------------------------------------------------------- #

def test_query_is_sent():
    with patch("requests.get", return_value=_ok([_hit(139.7, 35.5)])) as get:
        _client().geocode("横浜市中区日本大通1")
    assert get.call_args.kwargs["params"]["q"] == "横浜市中区日本大通1"


def test_request_has_timeout_and_user_agent():
    """公共APIなので素性を明示し、タイムアウトも設定する。"""
    with patch("requests.get", return_value=_ok([_hit(139.7, 35.5)])) as get:
        _client().geocode("川崎")
    assert get.call_args.kwargs["timeout"] > 0
    assert "User-Agent" in get.call_args.kwargs["headers"]


# --- エラー処理 -------------------------------------------------------------- #

@pytest.mark.parametrize("status", [429, 500, 503])
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


def test_non_list_response_becomes_geocoding_error():
    with patch("requests.get", return_value=_ok({"unexpected": True})):
        with pytest.raises(geocoding.GeocodingError):
            _client().geocode("川崎")
