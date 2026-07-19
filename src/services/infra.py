"""インフラ・医療資源データの取得/フィルタリングサービス。

病院・避難所・トイレ・人口密度メッシュの各データセットを DataStore から読み込み、
半径・疾患・設備などの条件で絞り込むロジックを提供する。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..datastore import DataStore, get_datastore
from .geo import haversine_km

logger = logging.getLogger(__name__)

# データセット名 → リソース(ファイル)名 のマッピング
DATASET_RESOURCES: Dict[str, str] = {
    "hospitals": "hospitals",
    "evacuation": "evacuation_sites",
    "toilets": "toilets",
    "landprice": "landprice",
    # 町名別の集計。`scripts/clean_landprice.py` がビルド時に生成する。
    "landprice_areas": "landprice_areas",
}

# 人口メッシュの解像度 → リソース(ファイル)名。
# 解像度は元データ(KEY_CODE の桁数)で決まるため、1km のデータから 500m を
# 作り出すことはできない。対応する CSV を取り込んで
# `scripts/clean_mesh.py` を実行するとファイルが増え、自動的に選べるようになる。
MESH_RESOURCES: Dict[str, str] = {
    "1km": "population_mesh_1km",
    "500m": "population_mesh_500m",
    "250m": "population_mesh_250m",
    "125m": "population_mesh_125m",
}

# 細かい順（UI の並び順と既定値の決定に使う）
MESH_RESOLUTIONS_FINEST_FIRST = ["125m", "250m", "500m", "1km"]


def available_mesh_resolutions(store: Optional[DataStore] = None) -> List[str]:
    """データが実在する解像度の一覧を粗い順で返す。"""
    store = store or get_datastore()
    return [
        resolution
        for resolution in reversed(MESH_RESOLUTIONS_FINEST_FIRST)
        if store.exists(MESH_RESOURCES[resolution])
    ]


def default_mesh_resolution(store: Optional[DataStore] = None) -> Optional[str]:
    """既定の解像度。利用可能なうち最も粗いもの（描画が最も軽い）を選ぶ。

    `available_mesh_resolutions` は粗い順に返すため先頭が最も粗い。
    """
    available = available_mesh_resolutions(store)
    return available[0] if available else None


def load_mesh(
    resolution: Optional[str] = None,
    store: Optional[DataStore] = None,
) -> List[Dict[str, Any]]:
    """指定解像度の人口メッシュを読み込む。

    Args:
        resolution: "1km" / "500m" / "250m"。None なら既定の解像度。

    Raises:
        KeyError: 未知の解像度名、または該当データが存在しない場合。
    """
    store = store or get_datastore()

    if resolution is None:
        resolution = default_mesh_resolution(store)
        if resolution is None:
            raise KeyError("利用可能な人口メッシュデータがありません")

    if resolution not in MESH_RESOURCES:
        raise KeyError(f"unknown mesh resolution: {resolution!r}")

    resource = MESH_RESOURCES[resolution]
    if not store.exists(resource):
        raise KeyError(f"mesh data not available for resolution: {resolution!r}")

    return store.load_json(resource)


def load_dataset(name: str, store: Optional[DataStore] = None) -> List[Dict[str, Any]]:
    """指定データセットを読み込んでリストで返す。"""
    if name not in DATASET_RESOURCES:
        raise KeyError(f"unknown dataset: {name!r}")
    store = store or get_datastore()
    return store.load_json(DATASET_RESOURCES[name])


def _location_of(item: Dict[str, Any]) -> Optional[tuple]:
    loc = item.get("location")
    if not loc:
        return None
    try:
        return float(loc["lat"]), float(loc["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def filter_by_radius(
    items: List[Dict[str, Any]],
    lat: float,
    lng: float,
    radius_km: float,
) -> List[Dict[str, Any]]:
    """中心座標からの半径(km)内の要素を、距離昇順で返す。

    各要素には `distanceKm` フィールドを付与する(元データは破壊しない)。
    """
    results: List[Dict[str, Any]] = []
    for item in items:
        loc = _location_of(item)
        if loc is None:
            continue
        distance = haversine_km(lat, lng, loc[0], loc[1])
        if distance <= radius_km:
            enriched = dict(item)
            enriched["distanceKm"] = round(distance, 3)
            results.append(enriched)
    results.sort(key=lambda x: x["distanceKm"])
    return results


def filter_hospitals_by_capability(
    hospitals: List[Dict[str, Any]],
    capability: str,
) -> List[Dict[str, Any]]:
    """指定の対応能力(持病カテゴリ等)を持つ病院のみ返す。"""
    needle = capability.strip()
    if not needle:
        return list(hospitals)
    return [h for h in hospitals if needle in h.get("capabilities", [])]


def filter_by_max_price(
    lands: List[Dict[str, Any]],
    max_price: Optional[float],
) -> List[Dict[str, Any]]:
    """地価の上限(円/㎡)以下の地点のみ返す。上限なしなら全件。"""
    if max_price is None:
        return list(lands)
    return [land for land in lands if land.get("pricePerSqm", 0) <= max_price]


def summarize_areas_by_town(
    lands: List[Dict[str, Any]],
    shelters: List[Dict[str, Any]],
    hospitals: List[Dict[str, Any]],
    radius_km: float = 0.8,
) -> List[Dict[str, Any]]:
    """町名ごとに「地価の目安」と「徒歩圏の防災施設数」をまとめる。

    「地価が安くて避難所が近いエリア」のような質問に、AIが数値で
    答えられるようにするための集計。地価公示は地点の価格なので、
    同じ町に複数地点あるときは平均を取る。

    Args:
        radius_km: 各地点から施設を数える半径。0.8km は徒歩10分の目安。

    Returns:
        地価の安い順に並べたエリア一覧。
    """
    by_town: Dict[str, Dict[str, Any]] = {}

    for land in lands:
        town = land.get("town") or "（町名不明）"
        location = _location_of(land)
        if location is None:
            continue

        bucket = by_town.setdefault(
            town, {"town": town, "prices": [], "shelters": 0, "hospitals": 0, "points": 0}
        )
        bucket["prices"].append(land.get("pricePerSqm", 0))
        bucket["points"] += 1
        bucket["shelters"] += len(filter_by_radius(shelters, location[0], location[1], radius_km))
        bucket["hospitals"] += len(filter_by_radius(hospitals, location[0], location[1], radius_km))

    areas: List[Dict[str, Any]] = []
    for bucket in by_town.values():
        prices = bucket["prices"] or [0]
        points = max(bucket["points"], 1)
        areas.append(
            {
                "town": bucket["town"],
                "avgPricePerSqm": round(sum(prices) / len(prices)),
                "minPricePerSqm": min(prices),
                "maxPricePerSqm": max(prices),
                "landPoints": bucket["points"],
                # 地点ごとの平均にして、地点数の多い町が有利にならないようにする
                "sheltersNearby": round(bucket["shelters"] / points, 1),
                "hospitalsNearby": round(bucket["hospitals"] / points, 1),
                "radiusKm": radius_km,
            }
        )

    areas.sort(key=lambda a: a["avgPricePerSqm"])
    return areas


def load_landprice_areas(store: Optional[DataStore] = None) -> List[Dict[str, Any]]:
    """町名別の地価×防災施設の集計を読む。

    集計は `scripts/clean_landprice.py` がビルド時に作る。
    毎リクエスト計算すると 1,787地点 × 4,265避難所 の距離計算で50秒超かかるため。

    集計ファイルが無い場合はその場で計算する（遅いが動作は止めない）。
    """
    store = store or get_datastore()

    if store.exists(DATASET_RESOURCES["landprice_areas"]):
        return store.load_json(DATASET_RESOURCES["landprice_areas"])

    logger.warning(
        "集計済みの地価エリアが見つかりません。その場で計算します（遅い）。"
        " scripts/clean_landprice.py を実行してください。"
    )
    return summarize_areas_by_town(
        load_dataset("landprice", store),
        load_dataset("evacuation", store),
        load_dataset("hospitals", store),
    )


def filter_multifunction_toilets(toilets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """多機能トイレ(車椅子対応 or オストメイト対応)のみ返す。"""
    result = []
    for t in toilets:
        attrs = t.get("attributes", {})
        if attrs.get("accessible") or attrs.get("ostomate"):
            result.append(t)
    return result
