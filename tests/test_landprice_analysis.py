"""地価×防災施設の集計ロジックのテスト。

「地価が安くて避難所が近いエリア」に答えるための材料を作る部分。
AIに計算させると誤るため、数値の集計はサーバ側で行う。
"""

from __future__ import annotations

from src.services import infra

LANDS = [
    {"id": "l1", "town": "千鳥町", "pricePerSqm": 92000, "location": {"lat": 35.500, "lng": 139.750}},
    {"id": "l2", "town": "中瀬", "pricePerSqm": 272000, "location": {"lat": 35.520, "lng": 139.720}},
    {"id": "l3", "town": "中瀬", "pricePerSqm": 300000, "location": {"lat": 35.521, "lng": 139.721}},
    {"id": "l4", "town": "駅前本町", "pricePerSqm": 4850000, "location": {"lat": 35.531, "lng": 139.697}},
]
SHELTERS = [
    {"id": "s1", "name": "中瀬小学校", "location": {"lat": 35.5205, "lng": 139.7205}},
    {"id": "s2", "name": "中瀬中学校", "location": {"lat": 35.5215, "lng": 139.7215}},
]
HOSPITALS = [
    {"id": "h1", "name": "中瀬病院", "location": {"lat": 35.5202, "lng": 139.7202}},
]


def test_filter_by_max_price():
    out = infra.filter_by_max_price(LANDS, 300000)
    assert {l["id"] for l in out} == {"l1", "l2", "l3"}


def test_filter_by_max_price_without_limit_returns_all():
    assert len(infra.filter_by_max_price(LANDS, None)) == len(LANDS)


def test_filter_by_max_price_does_not_mutate():
    copy = [dict(l) for l in LANDS]
    infra.filter_by_max_price(LANDS, 100000)
    assert LANDS == copy


def test_summarize_groups_by_town():
    areas = infra.summarize_areas_by_town(LANDS, SHELTERS, HOSPITALS)
    assert {a["town"] for a in areas} == {"千鳥町", "中瀬", "駅前本町"}


def test_summarize_averages_price_within_town():
    """同じ町に複数地点あるときは平均を取る（地価公示は地点の価格のため）。"""
    areas = {a["town"]: a for a in infra.summarize_areas_by_town(LANDS, SHELTERS, HOSPITALS)}
    naka = areas["中瀬"]
    assert naka["avgPricePerSqm"] == 286000  # (272000+300000)/2
    assert naka["minPricePerSqm"] == 272000
    assert naka["maxPricePerSqm"] == 300000
    assert naka["landPoints"] == 2


def test_summarize_counts_nearby_facilities():
    areas = {a["town"]: a for a in infra.summarize_areas_by_town(LANDS, SHELTERS, HOSPITALS)}
    assert areas["中瀬"]["sheltersNearby"] > 0
    assert areas["中瀬"]["hospitalsNearby"] > 0
    # 遠い町には施設が無い
    assert areas["千鳥町"]["sheltersNearby"] == 0


def test_facility_count_is_averaged_per_point():
    """地点数の多い町が単純合計で有利にならないこと。"""
    areas = {a["town"]: a for a in infra.summarize_areas_by_town(LANDS, SHELTERS, HOSPITALS)}
    # 中瀬は2地点あるが、1地点あたりの平均で表す
    assert areas["中瀬"]["sheltersNearby"] <= len(SHELTERS)


def test_sorted_by_price_ascending():
    areas = infra.summarize_areas_by_town(LANDS, SHELTERS, HOSPITALS)
    prices = [a["avgPricePerSqm"] for a in areas]
    assert prices == sorted(prices)


def test_radius_is_configurable_and_reported():
    areas = infra.summarize_areas_by_town(LANDS, SHELTERS, HOSPITALS, radius_km=2.0)
    assert all(a["radiusKm"] == 2.0 for a in areas)


def test_handles_empty_inputs():
    assert infra.summarize_areas_by_town([], [], []) == []


def test_skips_land_without_location():
    broken = [{"id": "x", "town": "座標なし", "pricePerSqm": 100}]
    assert infra.summarize_areas_by_town(broken, SHELTERS, HOSPITALS) == []


# ---------------------------------------------------------------------------
# 集計はビルド時に済ませる（毎リクエスト計算すると50秒超かかる）
# ---------------------------------------------------------------------------

def test_load_landprice_areas_reads_prebuilt_file():
    areas = infra.load_landprice_areas()
    assert [a["town"] for a in areas] == ["千鳥町", "中瀬", "駅前本町"]


def test_prebuilt_areas_have_expected_shape():
    for area in infra.load_landprice_areas():
        assert set(area) >= {
            "town", "avgPricePerSqm", "sheltersNearby", "hospitalsNearby", "radiusKm",
        }


def test_load_landprice_areas_falls_back_when_missing(monkeypatch, tmp_path):
    """集計ファイルが無くても止まらず、その場で計算する。"""
    import json as _json
    import shutil

    from src import config

    # 集計ファイルだけ欠いたデータディレクトリを用意する
    src_dir = config.local_data_dir()
    for name in ("landprice", "evacuation_sites", "hospitals"):
        shutil.copy(f"{src_dir}/{name}.json", tmp_path / f"{name}.json")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))

    areas = infra.load_landprice_areas()
    assert isinstance(areas, list)
    assert areas  # 計算結果が返る
