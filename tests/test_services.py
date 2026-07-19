"""サービス層 (geo / geocoding / infra) の単体テスト。"""

import pytest

from src.services.geo import haversine_km
from src.services.geocoding import MockGeocoder
from src.services import infra


def test_haversine_zero_distance():
    assert haversine_km(35.5, 139.7, 35.5, 139.7) == pytest.approx(0.0, abs=1e-9)


def test_haversine_known_distance():
    # 緯度1度 ≒ 111km
    d = haversine_km(35.0, 139.0, 36.0, 139.0)
    assert d == pytest.approx(111.19, abs=0.5)


def test_mock_geocoder_matches_and_falls_back():
    g = MockGeocoder()
    assert g.geocode("東京駅前") == {"lat": 35.6812, "lng": 139.7671}
    # 未知の住所は default へ
    assert g.geocode("どこでもない場所") == {"lat": 35.512, "lng": 139.716}
    # 空文字は None
    assert g.geocode("") is None


def test_load_dataset_unknown_raises():
    with pytest.raises(KeyError):
        infra.load_dataset("unknown_dataset")


def test_filter_by_radius_enriches_and_sorts():
    items = [
        {"id": "a", "location": {"lat": 35.520, "lng": 139.716}},
        {"id": "b", "location": {"lat": 35.512, "lng": 139.716}},
        {"id": "c", "location": {"lat": 35.900, "lng": 139.716}},  # 遠方
        {"id": "d"},  # location 無し → 除外
    ]
    out = infra.filter_by_radius(items, 35.512, 139.716, radius_km=5)
    ids = [x["id"] for x in out]
    assert ids == ["b", "a"]  # 近い順、遠方cと位置無しdは除外
    assert all("distanceKm" in x for x in out)


def test_filter_multifunction_toilets():
    toilets = [
        {"id": "1", "attributes": {"accessible": True, "ostomate": False}},
        {"id": "2", "attributes": {"accessible": False, "ostomate": True}},
        {"id": "3", "attributes": {"accessible": False, "ostomate": False}},
    ]
    out = infra.filter_multifunction_toilets(toilets)
    assert {t["id"] for t in out} == {"1", "2"}
