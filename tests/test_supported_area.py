"""対応エリア（神奈川県）外の住所を弾くテスト。

避難所・医療機関・トイレのデータは神奈川県分しか無い。
県外の座標を基準にすると「周辺に施設が0件」の地図とAI回答が返り、
利用者は「該当なし」なのか「対象外」なのか区別できない。
そのため住所検索の時点で対象外と伝える。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import function_app
from src.services import geocoding


def _gsi_response(title, lng=139.6425, lat=35.4478):
    response = MagicMock(status_code=200, ok=True)
    response.json.return_value = [
        {"geometry": {"coordinates": [lng, lat], "type": "Point"},
         "properties": {"title": title, "addressCode": ""}}
    ]
    return response


# --- 都道府県の判定 ---------------------------------------------------------- #

@pytest.mark.parametrize("title,expected", [
    ("神奈川県横浜市中区日本大通１番地", "神奈川県"),
    ("東京都千代田区丸の内一丁目９番", "東京都"),
    ("大阪府大阪市北区梅田三丁目１番", "大阪府"),
    ("北海道札幌市中央区南一条西", "北海道"),
    ("京都府京都市中京区", "京都府"),
])
def test_extracts_prefecture_from_title(title, expected):
    """国土地理院の title は必ず都道府県から始まる（addressCode は空で使えない）。"""
    assert geocoding.extract_prefecture(title) == expected


def test_extracts_none_from_unexpected_title():
    assert geocoding.extract_prefecture("よくわからない文字列") is None
    assert geocoding.extract_prefecture("") is None
    assert geocoding.extract_prefecture(None) is None


def test_supported_prefecture_is_kanagawa():
    assert geocoding.SUPPORTED_PREFECTURE == "神奈川県"


@pytest.mark.parametrize("title,ok", [
    ("神奈川県川崎市川崎区", True),
    ("神奈川県横浜市中区", True),
    ("東京都大田区", False),
    ("静岡県熱海市", False),
])
def test_is_supported_area(title, ok):
    assert geocoding.is_supported_area(title) is ok


# --- ジオコーダの戻り値 ------------------------------------------------------ #

def test_geocode_returns_prefecture_and_title():
    """呼び出し側が対象エリアを判定できるよう、都道府県も返す。"""
    with patch("requests.get", return_value=_gsi_response("神奈川県横浜市中区日本大通１番地")):
        point = geocoding.GsiGeocoder().geocode("横浜市中区日本大通1")
    assert point["lat"] == 35.4478
    assert point["prefecture"] == "神奈川県"
    assert "日本大通" in point["title"]


def test_geocode_still_returns_out_of_area_result():
    """ジオコーダ自身は弾かない（対象エリアの判断は呼び出し側の責務）。"""
    with patch("requests.get", return_value=_gsi_response("東京都千代田区丸の内一丁目９番")):
        point = geocoding.GsiGeocoder().geocode("東京都千代田区丸の内1-9-1")
    assert point["prefecture"] == "東京都"


# --- ルート ------------------------------------------------------------------ #

def test_geocode_route_accepts_kanagawa(invoke, monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "gsi")
    with patch("requests.get", return_value=_gsi_response("神奈川県横浜市中区日本大通１番地")):
        status, body = invoke(function_app.geocode, params={"address": "横浜市中区日本大通1"})
    assert status == 200
    assert body["location"]["lat"] == 35.4478


def test_geocode_route_rejects_outside_kanagawa(invoke, monkeypatch):
    """県外は 422 とし、対応エリアを明示して入力し直しを促す。"""
    monkeypatch.setenv("GEOCODER_BACKEND", "gsi")
    with patch("requests.get", return_value=_gsi_response("東京都千代田区丸の内一丁目９番")):
        status, body = invoke(function_app.geocode, params={"address": "東京都千代田区丸の内1-9-1"})
    assert status == 422
    assert "神奈川県" in body["error"]
    # どこが対象外だったか分かるようにする
    assert "東京都" in body["error"]


def test_rejection_message_tells_user_what_to_do(invoke, monkeypatch):
    monkeypatch.setenv("GEOCODER_BACKEND", "gsi")
    with patch("requests.get", return_value=_gsi_response("大阪府大阪市北区梅田三丁目１番")):
        _, body = invoke(function_app.geocode, params={"address": "大阪市北区梅田3-1-3"})
    assert "入力" in body["error"]


def test_geocode_route_allows_unknown_prefecture(invoke, monkeypatch):
    """都道府県を判定できない場合は通す（誤って弾くより良い）。"""
    monkeypatch.setenv("GEOCODER_BACKEND", "gsi")
    with patch("requests.get", return_value=_gsi_response("判別できない住所表記")):
        status, _ = invoke(function_app.geocode, params={"address": "なにか"})
    assert status == 200


def test_mock_geocoder_has_no_prefecture(monkeypatch):
    """他のジオコーダ実装は prefecture を持たないが、ルートは落ちないこと。"""
    monkeypatch.setenv("GEOCODER_BACKEND", "mock")
    point = geocoding.get_geocoder().geocode("川崎")
    assert "lat" in point and "lng" in point
