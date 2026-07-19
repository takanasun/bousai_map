"""ヘルスチェックルートのテスト。Functions のルートが正しく応答するかを検証する。"""

import function_app


def test_health_returns_200_ok(invoke):
    status, body = invoke(function_app.health)
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == "bousai-map-api"
