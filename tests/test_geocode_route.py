"""ジオコーディングルート (/api/geocode) のテスト。"""

import function_app


def test_geocode_known_address(invoke):
    status, body = invoke(function_app.geocode, params={"address": "中央町2-3-1"})
    assert status == 200
    assert body["address"] == "中央町2-3-1"
    # "中央町" エントリに一致するはず
    assert body["location"]["lat"] == 35.512
    assert body["location"]["lng"] == 139.715


def test_geocode_unknown_address_falls_back_to_default(invoke):
    status, body = invoke(function_app.geocode, params={"address": "存在しない町名XYZ"})
    assert status == 200
    # default 座標にフォールバック
    assert body["location"] == {"lat": 35.512, "lng": 139.716}


def test_geocode_missing_address_returns_400(invoke):
    status, body = invoke(function_app.geocode, params={})
    assert status == 400
    assert "error" in body
