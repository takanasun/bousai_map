#!/usr/bin/env python3
"""国土数値情報「地価公示」(L01) から対象エリアの地価データを抽出する。

防災施設データと掛け合わせて「地価が安く、避難所が近いエリア」を
比較できるようにするための下ごしらえ。

    使い方:
        python scripts/clean_landprice.py                      # 県全域
        python scripts/clean_landprice.py --city-codes 14131   # 川崎区のみ
        python scripts/clean_landprice.py --input data/raw/kanagawa_pref/L01-21_14.geojson

出力スキーマ (1要素):
    {
      "id": "land_14131_0001",
      "address": "神奈川県川崎市川崎区中瀬2-17-12",
      "town": "中瀬",
      "location": { "lat": 35.52128, "lng": 139.67347 },
      "pricePerSqm": 272000,
      "uses": ["住宅"],
      "year": "2021"
    }

このデータ固有の「罠」と対策:
    1. プロパティ名が `L01_006` のようなコードで意味が読めない
       → 定数に意味のある名前を付けて対応表をコメントに残す
    2. 住所に全角スペースが入る（"神奈川県　川崎市…"）
       → 除去する（表示と町名の突合のため）
    3. 用途は "住宅,店舗" のようなカンマ区切りの複合値
       → 配列に分解する
    4. 神奈川県全域1,787地点のうち川崎区は41地点
       → 市区町村コードで絞り込む

注意:
    地価公示は「地点」の価格であり、町丁目全体の平均ではない。
    同じ町でも地点により差があるため、エリアの傾向を示す目安として扱うこと。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

# 町名別の集計はアプリ側(`src/services/infra.py`)と同じ関数を使う。
# ここに書き写すと、片方だけ直したときに API と生成データがずれる。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.services.infra import summarize_areas_by_town  # noqa: E402

logger = logging.getLogger("clean_landprice")

# --- 定数 ------------------------------------------------------------------

DEFAULT_INPUT = os.path.join(
    PROJECT_ROOT, "data", "raw", "kanagawa_pref", "L01-21_14.geojson"
)
# 出力ファイル名は `src/services/infra.py` の DATASET_RESOURCES と一致させること
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "data", "processed", "landprice.json")

# 町名別の集計結果のファイル名。API はこれを読むだけにする
# （毎リクエスト集計すると 1,787地点 × 4,265避難所 の距離計算で50秒以上かかる）。
AREAS_FILENAME = "landprice_areas.json"

DEFAULT_AREAS_OUTPUT = os.path.join(PROJECT_ROOT, "data", "processed", AREAS_FILENAME)


def _default_areas_path(output_path: str) -> str:
    """集計の出力先は、地点データの出力先と同じディレクトリに置く。

    グローバル定数を既定にすると、テストが tmp_path へ出力しても集計だけ
    本番の data/processed へ書き込まれ、実データを壊す（実際に壊した）。
    """
    return os.path.join(os.path.dirname(os.path.abspath(output_path)), AREAS_FILENAME)

# 集計には既存の加工済みデータを使う（先に clean_shelters / clean_hospitals を実行しておく）。
# 読み込み先は出力先と同じディレクトリ。

# L01 のプロパティコード → 意味
PRICE_KEY = "L01_006"       # 価格（円/㎡）
CITY_CODE_KEY = "L01_021"   # 市区町村コード
ADDRESS_KEY = "L01_023"     # 所在地
USE_KEY = "L01_025"         # 利用現況（カンマ区切りの複合値）
YEAR_KEY = "L01_005"        # 調査年

# 既定は県全域（None = 絞り込みなし）。特定の市区に限定したいときだけ指定する。
DEFAULT_CITY_CODES: Optional[List[str]] = None

# 町名の後ろに続く要素（丁目・番地）を切り落とすためのパターン。
# 全角数字・漢数字・ハイフン類のいずれかが現れたらそこまでを町名とみなす。
_TOWN_TAIL = re.compile(r"[０-９0-9一二三四五六七八九十〇丁目−\-–—―番地].*$")

# 市区町村の区切り。県全域では区を持たない市町村が4割を占めるため、
# 「区」だけを見ると町名が空になる（例: 横須賀市池田町、愛甲郡愛川町角田）。
# 「郡」は市町村より前に来るので除外し、最も後ろに現れたものを境界とする。
_MUNICIPALITY_MARKERS = ("区", "市", "町", "村")


def _normalize_address(value: Any) -> str:
    """全角スペースを除去し、表示・突合に使える形に整える。"""
    if value is None:
        return ""
    text = str(value).replace("　", "").strip()
    return "" if text == "nan" else text


def extract_town(address: str) -> str:
    """住所から町名を取り出す。

    「神奈川県川崎市川崎区中瀬２−１７−１２」→「中瀬」
    「神奈川県横須賀市池田町６−１０−１０」   →「池田町」
    「神奈川県愛甲郡愛川町角田２１０」       →「角田」

    市区町村マーカー（区/市/町/村）のうち **最も後ろ** のものを境界にする。
    「愛甲郡愛川町」のように複数現れる場合、最後が実際の市町村名の末尾になる。
    町名自体が「〜町」で終わる場合（池田町）は、マーカーの後ろが空になるので
    ひとつ前のマーカーまで戻って判定する。
    """
    if not address:
        return ""

    # マーカーの出現位置を後ろから順に試す
    positions = sorted(
        {index for marker in _MUNICIPALITY_MARKERS for index in _find_all(address, marker)},
        reverse=True,
    )
    for index in positions:
        candidate = _TOWN_TAIL.sub("", address[index + 1 :]).strip()
        if candidate:
            return candidate
    return ""


def _find_all(text: str, needle: str) -> List[int]:
    """`needle` の出現位置をすべて返す。"""
    positions: List[int] = []
    start = text.find(needle)
    while start >= 0:
        positions.append(start)
        start = text.find(needle, start + 1)
    return positions


def _parse_price(value: Any) -> Optional[int]:
    """価格（円/㎡）を整数で返す。欠損・0以下は None。"""
    text = str(value).strip() if value is not None else ""
    if not text or text == "nan":
        return None
    try:
        price = int(float(text))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _parse_uses(value: Any) -> List[str]:
    """利用現況をカンマ区切りから配列へ。"""
    text = _normalize_address(value)
    if not text or text == "_":
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _parse_position(geometry: Any) -> Optional[Dict[str, float]]:
    """GeoJSON の [経度, 緯度] を {lat, lng} に。不正なら None。"""
    try:
        coordinates = geometry["coordinates"]
        lng, lat = float(coordinates[0]), float(coordinates[1])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not (20.0 <= lat <= 46.0 and 122.0 <= lng <= 154.0):
        return None
    return {"lat": round(lat, 6), "lng": round(lng, 6)}


# --- 本体 --------------------------------------------------------------------

def _load_json_list(path: str, label: str) -> List[Dict[str, Any]]:
    """加工済み JSON を読む。無ければ空リスト（集計をスキップする）。"""
    if not os.path.exists(path):
        logger.warning(
            "%sのデータが見つかりません: %s（先に対応するスクリプトを実行してください）",
            label,
            path,
        )
        return []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("%sのデータを読めませんでした: %s (%s)", label, path, exc)
        return []
    return data if isinstance(data, list) else []


def clean_landprice(
    input_path: str,
    output_path: str,
    city_codes: Optional[List[str]] = None,
    areas_output_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """地価公示 GeoJSON を読み込み、対象市区の地点を JSON へ書き出す。

    Args:
        input_path: L01 の GeoJSON。
        output_path: 出力 JSON のパス。
        city_codes: 残す市区町村コード。既定は川崎区。

    Returns:
        価格の安い順に並べたレコード一覧。

    Raises:
        FileNotFoundError: 入力ファイルが存在しない場合。
        ValueError: GeoJSON として読めない場合。
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_path}")

    targets = list(city_codes) if city_codes else None

    try:
        with open(input_path, "r", encoding="utf-8") as fp:
            body = json.load(fp)
    except json.JSONDecodeError as exc:
        logger.error("GeoJSON の解析に失敗しました: %s (%s)", input_path, exc)
        raise ValueError(f"GeoJSON として読めません: {input_path}") from exc

    features = body.get("features") or []
    total = len(features)

    records: List[Dict[str, Any]] = []
    skipped_price = 0
    skipped_position = 0

    for feature in features:
        props = feature.get("properties") or {}

        if targets is not None and str(props.get(CITY_CODE_KEY, "")).strip() not in targets:
            continue

        price = _parse_price(props.get(PRICE_KEY))
        if price is None:
            skipped_price += 1
            continue

        position = _parse_position(feature.get("geometry") or {})
        if position is None:
            skipped_position += 1
            continue

        address = _normalize_address(props.get(ADDRESS_KEY))
        records.append(
            {
                "id": f"land_{str(props.get(CITY_CODE_KEY)).strip()}_{len(records) + 1:04d}",
                "address": address,
                "town": extract_town(address),
                "location": position,
                "pricePerSqm": price,
                "uses": _parse_uses(props.get(USE_KEY)),
                "year": _normalize_address(props.get(YEAR_KEY)),
            }
        )

    # 安い順に並べる（「地価が安いエリア」の質問で先頭から使えるように）
    records.sort(key=lambda r: r["pricePerSqm"])

    out_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(records, fp, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("出力ファイルの書き込みに失敗しました: %s (%s)", output_path, exc)
        raise

    logger.info("全地点: %d", total)
    logger.info(
        "対象 %s: %d 地点",
        ",".join(targets) if targets else "神奈川県全域",
        len(records),
    )
    if skipped_price:
        logger.info("価格欠損で除外: %d 件", skipped_price)
    if skipped_position:
        logger.info("座標不正で除外: %d 件", skipped_position)
    if records:
        prices = [r["pricePerSqm"] for r in records]
        logger.info(
            "価格: 最小 %s円 / 中央 %s円 / 最大 %s円 (円/㎡)",
            f"{prices[0]:,}",
            f"{prices[len(prices) // 2]:,}",
            f"{prices[-1]:,}",
        )
        logger.info("町名: %d 種類", len({r["town"] for r in records if r["town"]}))
    logger.info("出力レコード数: %d → %s", len(records), output_path)

    # --- 町名別の集計をビルド時に済ませる ---
    # API 側で毎回計算すると 1,787地点 × 4,265避難所 の距離計算で 50秒超になる。
    areas_path = areas_output_path or _default_areas_path(output_path)
    processed_dir = os.path.dirname(os.path.abspath(output_path))
    shelters = _load_json_list(os.path.join(processed_dir, "evacuation_sites.json"), "避難所")
    hospitals = _load_json_list(os.path.join(processed_dir, "hospitals.json"), "医療機関")

    if shelters or hospitals:
        logger.info("町名別の集計を作成しています（時間がかかります）…")
        areas = summarize_areas_by_town(records, shelters, hospitals)
        try:
            with open(areas_path, "w", encoding="utf-8") as fp:
                json.dump(areas, fp, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("集計の書き込みに失敗しました: %s (%s)", areas_path, exc)
            raise
        logger.info("集計エリア数: %d → %s", len(areas), areas_path)
    else:
        logger.warning(
            "避難所・医療機関のデータが無いため町名別の集計をスキップしました。"
            " clean_shelters.py と clean_hospitals.py を実行してから再実行してください。"
        )

    return records


# --- CLI --------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="国土数値情報 地価公示(L01) から対象市区の地価を抽出する"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="L01 の GeoJSON")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="出力 JSON のパス")
    parser.add_argument(
        "--areas-output",
        default=DEFAULT_AREAS_OUTPUT,
        help="町名別集計の出力先（API はこれを読む）",
    )
    parser.add_argument(
        "--city-codes",
        nargs="+",
        default=DEFAULT_CITY_CODES,
        help="残す市区町村コード（既定: 指定なし＝県全域。例: 14131 = 川崎市川崎区）",
    )
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出力する")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info("地価データのクレンジングを開始します: %s", args.input)
    try:
        records = clean_landprice(
            input_path=args.input,
            output_path=args.output,
            city_codes=args.city_codes,
            areas_output_path=args.areas_output,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.error("クレンジングに失敗しました: %s", exc)
        return 1

    if not records:
        logger.warning("出力レコードが 0 件です。--city-codes を確認してください。")
        return 1

    logger.info("クレンジングが完了しました。出力先: %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
