"""インフラ系ルート (hospitals / evacuation / toilets / mesh) のテスト。

データ参照先は conftest の `use_fixture_data` フィクスチャにより
`tests/fixtures/` に固定される。`data/processed/` の実データ
(神奈川県全域の公衆トイレ等)を更新してもこれらのテストは影響を受けない。
期待値はできるだけフィクスチャファイルから導出し、二重管理を避ける。
"""

import os

import function_app
from src import config
from src.services import infra


def test_datastore_points_at_test_fixtures(fixtures_dir):
    """テストが実データではなくフィクスチャを見ていることの回帰ガード。

    これが破れると、データ更新のたびに他のテストが不可解に壊れる。
    """
    assert os.path.abspath(config.local_data_dir()) == os.path.abspath(fixtures_dir)


def test_hospitals_returns_all(invoke, load_fixture):
    expected = load_fixture("hospitals")
    status, body = invoke(function_app.hospitals)
    assert status == 200
    assert body["count"] == len(expected)
    assert len(body["items"]) == len(expected)


def test_hospitals_filter_by_capability(invoke, load_fixture):
    capability = "糖尿病"
    expected_ids = {
        h["id"] for h in load_fixture("hospitals") if capability in h.get("capabilities", [])
    }
    status, body = invoke(function_app.hospitals, params={"capability": capability})
    assert status == 200
    assert {h["id"] for h in body["items"]} == expected_ids


def test_hospitals_radius_filter_sorts_by_distance(invoke):
    status, body = invoke(
        function_app.hospitals,
        params={"lat": "35.512", "lng": "139.715", "radius": "2"},
    )
    assert status == 200
    # 半径2km内は中央付近の2病院のみ（北部の hosp_003 ≒2.7km は除外）
    ids = [h["id"] for h in body["items"]]
    assert "hosp_003" not in ids
    # distanceKm が付与され、昇順に並ぶ
    distances = [h["distanceKm"] for h in body["items"]]
    assert distances == sorted(distances)
    assert all(d <= 2 for d in distances)


def test_hospitals_invalid_number_returns_400(invoke):
    status, body = invoke(function_app.hospitals, params={"lat": "abc"})
    assert status == 400
    assert "error" in body


def test_evacuation_welfare_filter(invoke, load_fixture):
    sites = load_fixture("evacuation_sites")
    expected_ids = {e["id"] for e in sites if e.get("isWelfareShelter")}
    # フィルタが素通し/全除外になっていないこと（テスト自体の健全性チェック）
    assert 0 < len(expected_ids) < len(sites)

    status, body = invoke(function_app.evacuation, params={"welfare": "true"})
    assert status == 200
    assert body["count"] == len(expected_ids)
    assert {e["id"] for e in body["items"]} == expected_ids


def test_toilets_returns_all(invoke, load_fixture):
    expected = load_fixture("toilets")
    status, body = invoke(function_app.toilets)
    assert status == 200
    assert body["count"] == len(expected)


def test_toilets_multifunction_filter(invoke, load_fixture):
    """多機能トイレ(車椅子対応 or オストメイト対応)のみが返ること。

    期待値はフィクスチャから導出するため、フィクスチャを差し替えても壊れない。
    """
    toilets = load_fixture("toilets")
    expected_ids = {
        t["id"]
        for t in toilets
        if t["attributes"]["accessible"] or t["attributes"]["ostomate"]
    }
    # フィルタが素通しになっていないこと（テスト自体の健全性チェック）
    assert 0 < len(expected_ids) < len(toilets)

    status, body = invoke(function_app.toilets, params={"multifunction": "true"})
    assert status == 200
    assert {t["id"] for t in body["items"]} == expected_ids


def test_toilets_multifunction_filter_with_real_shaped_data():
    """実データ形状(OSM 由来 ID・既定名)でもフィルタが機能すること。

    `scripts/fetch_toilets.py` の出力は id が `toilet_n123` 形式、
    name の大半が既定値「公衆トイレ」になるため、その形でも検証しておく。
    """
    osm_like = [
        {
            "id": "toilet_n1001",
            "name": "公衆トイレ",
            "location": {"lat": 35.4478, "lng": 139.6425},
            "attributes": {"accessible": True, "ostomate": False, "open24h": True},
        },
        {
            "id": "toilet_w2001",
            "name": "小田原城址公園便所",
            "location": {"lat": 35.2, "lng": 139.1},
            "attributes": {"accessible": False, "ostomate": False, "open24h": False},
        },
    ]
    out = infra.filter_multifunction_toilets(osm_like)
    assert [t["id"] for t in out] == ["toilet_n1001"]


def test_mesh_returns_all(invoke, load_fixture):
    expected = load_fixture("population_mesh_1km")
    status, body = invoke(function_app.mesh)
    assert status == 200
    assert body["count"] == len(expected)
    assert body["items"][0]["meshId"] == expected[0]["meshId"]
