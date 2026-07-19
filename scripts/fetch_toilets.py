#!/usr/bin/env python3
"""神奈川県全域の公衆トイレデータを OpenStreetMap (Overpass API) から取得する。

docs/spec.md 「4.4. 公衆トイレデータ」のスキーマに合わせて整形し、
`data/processed/toilets.json` に保存する。

    使い方:
        python scripts/fetch_toilets.py                     # 通常実行
        python scripts/fetch_toilets.py --output /tmp/x.json
        python scripts/fetch_toilets.py --use-bbox          # エリア検索が不調な時

出力スキーマ (1要素):
    {
      "id": "toilet_n1001",
      "name": "横浜駅東口公衆トイレ",
      "location": { "lat": 35.4478, "lng": 139.6425 },
      "attributes": { "accessible": true, "ostomate": true, "open24h": true }
    }

備考:
    * Overpass API は認証不要の公開APIのため資格情報は扱わない。
      （将来 Azure Blob へ保存する際の接続文字列は `src/config.py` 経由で
      環境変数から読み込むこと。ここにハードコードしない。）
    * OSM の正式タグは `amenity=toilets`（複数形）。誤記の `amenity=toilet`
      も取りこぼさないよう、クエリは正規表現 `^toilets?$` で照合する。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

logger = logging.getLogger("fetch_toilets")

# --- 定数 ------------------------------------------------------------------

# プロジェクトルート (このファイルは <root>/scripts/fetch_toilets.py にある)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 既定の出力先。`src/services/infra.py` の DATASET_RESOURCES["toilets"] が
# 参照するファイル名（toilets.json）と一致させている。
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "data", "processed", "toilets.json")

# Overpass API のミラー。先頭から順に試し、失敗したら次へフェイルオーバーする。
DEFAULT_ENDPOINTS: Tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

# 対象エリア（OSM の行政界: 都道府県は admin_level=4）
DEFAULT_AREA_NAME = "神奈川県"

# エリア検索が使えない場合のフォールバック境界ボックス
# (south, west, north, east) — 神奈川県を包含する矩形
KANAGAWA_BBOX: Tuple[float, float, float, float] = (35.10, 138.90, 35.70, 139.80)

# 名前タグが無い場合の既定値
DEFAULT_NAME = "公衆トイレ"

# Overpass のサーバ側タイムアウト(秒) / HTTP のクライアント側タイムアウト(秒)
DEFAULT_QUERY_TIMEOUT = 180
DEFAULT_HTTP_TIMEOUT = 240

USER_AGENT = "bousai-map/1.0 (OSM public toilet data fetcher)"

# 真値とみなすタグ値
_TRUTHY_VALUES = frozenset({"yes", "designated"})

# 車椅子対応（多機能トイレ）を示すタグ
_ACCESSIBLE_TAGS = ("wheelchair", "toilets:wheelchair")

# オストメイト対応を示すタグ（日本の OSM で使われる表記ゆれを網羅）
_OSTOMATE_TAGS = (
    "ostomate",
    "toilets:ostomate",
    "amenity:ostomate",
    "ostomate_facility",
)

# 24時間営業とみなす opening_hours の値
_OPEN24H_PATTERN = re.compile(r"(24/7|00:00\s*-\s*24:00)", re.IGNORECASE)

# OSM 要素種別 → ID プレフィックス
_TYPE_PREFIX = {"node": "n", "way": "w", "relation": "r"}


class FetchError(RuntimeError):
    """データ取得・パース・保存に失敗したことを示す例外。"""


# --- クエリ生成 ------------------------------------------------------------


def build_query(
    area_name: Optional[str] = DEFAULT_AREA_NAME,
    bbox: Optional[Sequence[float]] = None,
    timeout: int = DEFAULT_QUERY_TIMEOUT,
) -> str:
    """Overpass QL クエリを組み立てる。

    Args:
        area_name: 行政界名で絞る場合の名称（例: "神奈川県"）。
        bbox: (south, west, north, east)。指定時は area_name より優先する。
        timeout: Overpass サーバ側の実行タイムアウト(秒)。

    Returns:
        Overpass QL のクエリ文字列。
    """
    # node / way / relation をすべて対象にし、way/relation は `out center` で
    # 代表点（重心）を取得する。
    selector = '["amenity"~"^toilets?$"]'

    if bbox is not None:
        south, west, north, east = (float(v) for v in bbox)
        scope = f"({south:g},{west:g},{north:g},{east:g})"
        area_clause = ""
    else:
        scope = "(area.target)"
        area_clause = (
            f'area["name"="{area_name}"]["admin_level"="4"]->.target;\n'
        )

    return (
        f"[out:json][timeout:{timeout}];\n"
        f"{area_clause}"
        f"(\n"
        f"  node{selector}{scope};\n"
        f"  way{selector}{scope};\n"
        f"  relation{selector}{scope};\n"
        f");\n"
        f"out center tags;\n"
    )


# --- タグ判定 --------------------------------------------------------------


def _is_yes(value: Any) -> bool:
    """OSM のタグ値が肯定（yes / designated）かどうか。"""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in _TRUTHY_VALUES


def _any_tag_is_yes(tags: Dict[str, Any], keys: Iterable[str]) -> bool:
    return any(_is_yes(tags.get(key)) for key in keys)


def detect_accessible(tags: Dict[str, Any]) -> bool:
    """多機能（車椅子対応）トイレかどうか。

    `wheelchair=limited` は部分対応にすぎないため False とし、確実に対応して
    いるものだけを True にする（避難時の判断を誤らせないため安全側に倒す）。
    """
    return _any_tag_is_yes(tags, _ACCESSIBLE_TAGS)


def detect_ostomate(tags: Dict[str, Any]) -> bool:
    """オストメイト対応設備を持つかどうか。"""
    return _any_tag_is_yes(tags, _OSTOMATE_TAGS)


def detect_open24h(tags: Dict[str, Any]) -> bool:
    """24時間利用可能かどうか（opening_hours から判定）。"""
    value = tags.get("opening_hours")
    if not isinstance(value, str):
        return False
    return bool(_OPEN24H_PATTERN.search(value.strip()))


# --- 変換 ------------------------------------------------------------------


def _extract_coordinates(element: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """要素から (lat, lng) を取り出す。node は直接、way/relation は center。"""
    if "lat" in element and "lon" in element:
        raw_lat, raw_lng = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        raw_lat, raw_lng = center.get("lat"), center.get("lon")

    if raw_lat is None or raw_lng is None:
        return None

    try:
        lat, lng = float(raw_lat), float(raw_lng)
    except (TypeError, ValueError):
        return None

    # 明らかな異常値（座標範囲外）を除外する
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return None

    return lat, lng


def _extract_name(tags: Dict[str, Any]) -> str:
    """表示名を決定する。name → name:ja → 既定値 の順にフォールバック。"""
    for key in ("name", "name:ja"):
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_NAME


def transform_element(element: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Overpass の1要素を docs/spec.md 4.4 のスキーマへ変換する。

    座標が取得できない要素は地図に描画できないため None を返す（呼び出し側で除外）。
    """
    coords = _extract_coordinates(element)
    if coords is None:
        logger.debug(
            "座標が無いため除外します: type=%s id=%s",
            element.get("type"),
            element.get("id"),
        )
        return None

    lat, lng = coords
    tags = element.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}

    prefix = _TYPE_PREFIX.get(str(element.get("type")), "x")
    osm_id = element.get("id")

    return {
        # OSM の要素種別+IDから生成する安定ID（再取得しても値が変わらない）
        "id": f"toilet_{prefix}{osm_id}",
        "name": _extract_name(tags),
        "location": {"lat": lat, "lng": lng},
        "attributes": {
            "accessible": detect_accessible(tags),
            "ostomate": detect_ostomate(tags),
            "open24h": detect_open24h(tags),
        },
    }


def parse_response(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Overpass の応答 JSON をクレンジング済みレコードのリストに変換する。

    Raises:
        FetchError: 応答に `elements` が含まれない（想定外の形式）場合。
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise FetchError("Overpass の応答形式が不正です（elements が見つかりません）")

    records: Dict[str, Dict[str, Any]] = {}
    skipped = 0

    for element in payload["elements"]:
        if not isinstance(element, dict):
            skipped += 1
            continue
        record = transform_element(element)
        if record is None:
            skipped += 1
            continue
        # 同一IDは後勝ちで上書き（重複排除）
        records[record["id"]] = record

    if skipped:
        logger.info("座標欠損等により %d 件を除外しました", skipped)

    # 差分を安定させるため ID 昇順で返す
    return [records[key] for key in sorted(records)]


# --- 取得 ------------------------------------------------------------------


def fetch_overpass(
    query: str,
    endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    session: Optional[Any] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    retries: int = 2,
    retry_wait: float = 5.0,
) -> Dict[str, Any]:
    """Overpass API にクエリを投げ、応答 JSON を返す。

    各エンドポイントを `retries` 回まで試し、駄目なら次のミラーへ切り替える。

    Raises:
        FetchError: すべてのエンドポイントで取得に失敗した場合。
    """
    session = session or requests.Session()
    last_error: Optional[str] = None

    for endpoint in endpoints:
        for attempt in range(1, retries + 1):
            try:
                logger.info(
                    "Overpass へ問い合わせます: %s (試行 %d/%d)",
                    endpoint,
                    attempt,
                    retries,
                )
                response = session.post(
                    endpoint,
                    data={"data": query},
                    timeout=timeout,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("応答が JSON オブジェクトではありません")
                return payload

            except (requests.RequestException, ValueError) as exc:
                # ValueError は json() のパース失敗（HTML エラーページ等）を含む
                last_error = f"{endpoint}: {exc}"
                logger.warning("取得に失敗しました (%s)", last_error)
                if attempt < retries and retry_wait > 0:
                    time.sleep(retry_wait)

    raise FetchError(f"すべての Overpass エンドポイントで取得に失敗しました: {last_error}")


# --- 保存 ------------------------------------------------------------------


def save_records(
    records: List[Dict[str, Any]],
    output_path: str,
    allow_empty: bool = False,
) -> None:
    """レコードを JSON ファイルへ保存する。

    取得結果が 0 件の場合、既存の正常なデータを空配列で壊さないよう既定で
    中断する（`allow_empty=True` で明示的に上書きできる）。

    Raises:
        FetchError: 0 件（allow_empty=False 時）または書き込みに失敗した場合。
    """
    if not records and not allow_empty:
        raise FetchError(
            "取得結果が 0 件のため保存を中止しました "
            "(意図的に空で上書きする場合は --allow-empty を指定してください)"
        )

    directory = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(directory, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(records, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
    except OSError as exc:
        logger.error("出力ファイルの書き込みに失敗しました: %s (%s)", output_path, exc)
        raise FetchError(f"failed to write output: {output_path}") from exc

    logger.info("%d 件を保存しました: %s", len(records), output_path)


def summarize(records: List[Dict[str, Any]]) -> str:
    """取得結果のサマリ文字列を返す（実行ログ用）。"""
    total = len(records)
    accessible = sum(1 for r in records if r["attributes"]["accessible"])
    ostomate = sum(1 for r in records if r["attributes"]["ostomate"])
    open24h = sum(1 for r in records if r["attributes"]["open24h"])
    named = sum(1 for r in records if r["name"] != DEFAULT_NAME)
    return (
        f"合計 {total} 件 / 車椅子対応 {accessible} 件 / "
        f"オストメイト {ostomate} 件 / 24時間 {open24h} 件 / 名称あり {named} 件"
    )


# --- CLI -------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="神奈川県全域の公衆トイレデータを OpenStreetMap から取得する",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"出力先 JSON のパス (既定: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--area-name",
        default=DEFAULT_AREA_NAME,
        help=f"対象の行政界名 (既定: {DEFAULT_AREA_NAME})",
    )
    parser.add_argument(
        "--use-bbox",
        action="store_true",
        help="行政界検索ではなく境界ボックスで取得する（エリア検索が不調な時のフォールバック）",
    )
    parser.add_argument(
        "--query-timeout",
        type=int,
        default=DEFAULT_QUERY_TIMEOUT,
        help=f"Overpass サーバ側のタイムアウト秒 (既定: {DEFAULT_QUERY_TIMEOUT})",
    )
    parser.add_argument(
        "--http-timeout",
        type=int,
        default=DEFAULT_HTTP_TIMEOUT,
        help=f"HTTP クライアント側のタイムアウト秒 (既定: {DEFAULT_HTTP_TIMEOUT})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="エンドポイントごとの試行回数 (既定: 2)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="取得件数が 0 でも空配列で上書きする",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="デバッグログを出力する",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """エントリポイント。成功で 0、失敗で 1 を返す。"""
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    query = build_query(
        area_name=None if args.use_bbox else args.area_name,
        bbox=KANAGAWA_BBOX if args.use_bbox else None,
        timeout=args.query_timeout,
    )
    logger.debug("Overpass QL:\n%s", query)

    try:
        payload = fetch_overpass(
            query,
            timeout=args.http_timeout,
            retries=args.retries,
        )
        records = parse_response(payload)
        logger.info(summarize(records))
        save_records(records, args.output, allow_empty=args.allow_empty)
    except FetchError as exc:
        logger.error("処理を中止しました: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
