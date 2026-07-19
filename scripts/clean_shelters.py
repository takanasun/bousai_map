#!/usr/bin/env python3
"""国土地理院の避難所データをクレンジングし、地図描画用 JSON に統合する。

災害時、避難所には性質のまったく異なる2種類がある:

    指定緊急避難場所 … 切迫した危険から「命を守る」ために逃げ込む場所。
                      洪水・地震・津波など **災害種別ごと** に指定される。
                      「地震では使えるが洪水では使えない」場所が存在する。
    指定避難所       … 自宅に戻れない人が一定期間「生活する」場所。
                      災害種別の指定は無く、受入対象者の定めがある。

同一施設が両方を兼ねることも多いため、施設名＋住所で名寄せして1件に統合し、
`isEmergencySite` / `isEvacuationCenter` の2フラグで役割を表現する。

    使い方:
        python scripts/clean_shelters.py
        python scripts/clean_shelters.py --input-dir "data/raw/ shelter"
        python scripts/clean_shelters.py --output data/processed/evacuation_sites.json

出力スキーマ (1要素):
    {
      "id": "evac_E1413000000001",
      "name": "川崎中学校",
      "address": "神奈川県横浜市中区日本大通1",
      "location": { "lat": 35.5231, "lng": 139.7215 },
      "isEmergencySite": true,       // 命を守る（指定緊急避難場所）
      "isEvacuationCenter": true,    // 生活する（指定避難所）
      "isWelfareShelter": false,     // 福祉避難所（要配慮者向け）
      "disasterTypes": ["flood", "earthquake", "fire"],
      "targetOccupants": ""
    }

元データ固有の「罠」と対策:
    1. ディレクトリ名と中身が入れ違っている
       （designated_emergency_evacuation_site/ の中身が実は指定避難所）
       → **ディレクトリ名を信用せず**、列の顔ぶれから種別を判定する
    2. 災害種別は該当時のみ "1"、非該当は空欄
    3. UTF-8 BOM 付き → encoding="utf-8-sig"
    4. 共通ID は2つのデータセット間で重複しない → 名寄せキーは施設名＋住所
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger("clean_shelters")

# --- 定数 ------------------------------------------------------------------

# プロジェクトルート (このファイルは <root>/scripts/clean_shelters.py にある)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 既定の入出力先。出力ファイル名は `src/services/infra.py` の
# DATASET_RESOURCES["evacuation"]（= "evacuation_sites"）と一致させること。
DEFAULT_INPUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", " shelter")
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "data", "processed", "evacuation_sites.json")

KIND_EMERGENCY = "emergency_site"   # 指定緊急避難場所（命を守る）
KIND_CENTER = "evacuation_center"   # 指定避難所（生活する）

# 災害対策基本法で定められた8種別。CSV 列名 → 出力キー
DISASTER_COLUMNS: Dict[str, str] = {
    "洪水": "flood",
    "崖崩れ、土石流及び地滑り": "landslide",
    "高潮": "stormSurge",
    "地震": "earthquake",
    "津波": "tsunami",
    "大規模な火事": "fire",
    "内水氾濫": "inlandFlood",
    "火山現象": "volcano",
}

# 指定避難所にのみ存在する列（種別判定に使う）
CENTER_MARKER_COLUMN = "受入対象者"

NAME_COLUMN = "施設・場所名"
ADDRESS_COLUMN = "住所"
ID_COLUMN = "共通ID"
LAT_COLUMN = "緯度"
LNG_COLUMN = "経度"

# 福祉避難所（要配慮者向け）を示すキーワード
WELFARE_KEYWORDS = ("要配慮者", "障害者", "高齢者", "乳幼児", "妊産婦", "配慮を要する")


# --- 種別判定 ---------------------------------------------------------------

def detect_kind(columns: Iterable[str]) -> str:
    """列の顔ぶれからデータセットの種別を判定する。

    ディレクトリ名は実データで入れ違っているため信用しない。

    Raises:
        ValueError: どちらの形式とも判定できない場合。
    """
    column_set = set(columns)

    if any(col in column_set for col in DISASTER_COLUMNS):
        return KIND_EMERGENCY
    if CENTER_MARKER_COLUMN in column_set:
        return KIND_CENTER

    raise ValueError(
        "避難所データの種別を判定できません。"
        f"災害種別列も {CENTER_MARKER_COLUMN!r} も存在しません: {sorted(column_set)[:10]}"
    )


# --- 各種抽出 ---------------------------------------------------------------

def _is_flagged(value: Any) -> bool:
    """該当フラグ（"1"）が立っているか。空欄・NaN は False。"""
    if value is None:
        return False
    text = str(value).strip()
    return text not in ("", "nan", "0")


def extract_disaster_types(row: Dict[str, Any]) -> List[str]:
    """対応する災害種別のキー一覧を返す（DISASTER_COLUMNS の定義順）。"""
    return [key for column, key in DISASTER_COLUMNS.items() if _is_flagged(row.get(column))]


def is_welfare_shelter(target_occupants: Any) -> bool:
    """受入対象者の記載から福祉避難所かどうかを判定する。"""
    if target_occupants is None:
        return False
    text = str(target_occupants).strip()
    if not text or text == "nan":
        return False
    return any(keyword in text for keyword in WELFARE_KEYWORDS)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text == "nan" else text


def _parse_coordinate(value: Any) -> Optional[float]:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if -180.0 <= number <= 180.0 else None


# --- 統合処理 ---------------------------------------------------------------

def _merge_key(name: str, address: str) -> str:
    """名寄せキー。共通IDは2データセット間で重複しないため施設名＋住所を使う。"""
    return f"{name}|{address}"


def _iter_csv_files(input_dir: str) -> List[str]:
    paths: List[str] = []
    for root, _dirs, files in os.walk(input_dir):
        for filename in sorted(files):
            if filename.lower().endswith(".csv"):
                paths.append(os.path.join(root, filename))
    return sorted(paths)


def clean_shelters(
    input_dir: str,
    output_path: str,
    encoding: str = "utf-8-sig",
) -> List[Dict[str, Any]]:
    """避難所 CSV 群を読み込み、統合した JSON を保存する。

    Args:
        input_dir: 避難所 CSV を含むディレクトリ（サブディレクトリも探索する）。
        output_path: 出力 JSON のパス。
        encoding: 入力ファイルの文字コード（国土地理院データは BOM 付き UTF-8）。

    Returns:
        統合後のレコード一覧。

    Raises:
        FileNotFoundError: 入力ディレクトリが存在しない場合。
    """
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"入力ディレクトリが見つかりません: {input_dir}")

    csv_paths = _iter_csv_files(input_dir)
    if not csv_paths:
        raise FileNotFoundError(f"CSV が1件も見つかりません: {input_dir}")

    merged: Dict[str, Dict[str, Any]] = {}
    stats = {"rows": 0, "skipped_no_coords": 0, "merged": 0}

    for path in csv_paths:
        try:
            df = pd.read_csv(path, encoding=encoding, dtype=str)
        except UnicodeDecodeError:
            logger.error("文字コードの解釈に失敗しました: %s (encoding=%s)", path, encoding)
            raise
        except pd.errors.ParserError as exc:
            logger.error("CSV の解析に失敗しました: %s (%s)", path, exc)
            raise

        kind = detect_kind(df.columns)
        logger.info(
            "%s: %d 行 (%s として読み込み)",
            os.path.relpath(path, input_dir),
            len(df),
            "指定緊急避難場所" if kind == KIND_EMERGENCY else "指定避難所",
        )

        for row in df.to_dict("records"):
            stats["rows"] += 1

            name = _clean_text(row.get(NAME_COLUMN))
            address = _clean_text(row.get(ADDRESS_COLUMN))
            lat = _parse_coordinate(row.get(LAT_COLUMN))
            lng = _parse_coordinate(row.get(LNG_COLUMN))

            if not name or lat is None or lng is None:
                stats["skipped_no_coords"] += 1
                continue

            key = _merge_key(name, address)
            record = merged.get(key)

            if record is None:
                record = {
                    "id": f"evac_{_clean_text(row.get(ID_COLUMN)) or f'{len(merged) + 1:06d}'}",
                    "name": name,
                    "address": address,
                    "location": {"lat": lat, "lng": lng},
                    "isEmergencySite": False,
                    "isEvacuationCenter": False,
                    "isWelfareShelter": False,
                    "disasterTypes": [],
                    "targetOccupants": "",
                }
                merged[key] = record
            else:
                stats["merged"] += 1

            if kind == KIND_EMERGENCY:
                record["isEmergencySite"] = True
                # 同一施設が複数行に分かれている場合に備え、災害種別は和集合を取る
                for disaster in extract_disaster_types(row):
                    if disaster not in record["disasterTypes"]:
                        record["disasterTypes"].append(disaster)
            else:
                record["isEvacuationCenter"] = True
                target = _clean_text(row.get(CENTER_MARKER_COLUMN))
                if target:
                    record["targetOccupants"] = target
                if is_welfare_shelter(target):
                    record["isWelfareShelter"] = True

    records = list(merged.values())

    # 災害種別は定義順に整列させ、出力の安定性を保つ
    order = list(DISASTER_COLUMNS.values())
    for record in records:
        record["disasterTypes"].sort(key=order.index)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(records, fp, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("出力ファイルの書き込みに失敗しました: %s (%s)", output_path, exc)
        raise

    logger.info("読み込み行数: %d", stats["rows"])
    if stats["skipped_no_coords"]:
        logger.info("座標・名称の欠損で除外: %d 件", stats["skipped_no_coords"])
    logger.info("名寄せで統合: %d 件", stats["merged"])
    logger.info(
        "内訳: 緊急避難場所 %d 件 / 指定避難所 %d 件 / 兼用 %d 件 / 福祉避難所 %d 件",
        sum(1 for r in records if r["isEmergencySite"]),
        sum(1 for r in records if r["isEvacuationCenter"]),
        sum(1 for r in records if r["isEmergencySite"] and r["isEvacuationCenter"]),
        sum(1 for r in records if r["isWelfareShelter"]),
    )
    logger.info("出力レコード数: %d → %s", len(records), output_path)

    return records


# --- CLI --------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="国土地理院の避難所 CSV を統合して地図描画用 JSON にする"
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR, help="避難所 CSV のディレクトリ")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="出力 JSON のパス")
    parser.add_argument("--encoding", default="utf-8-sig", help="入力ファイルの文字コード")
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出力する")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info("避難所データのクレンジングを開始します: %s", args.input_dir)
    try:
        records = clean_shelters(
            input_dir=args.input_dir,
            output_path=args.output,
            encoding=args.encoding,
        )
    except (FileNotFoundError, ValueError, UnicodeDecodeError, OSError) as exc:
        logger.error("クレンジングに失敗しました: %s", exc)
        return 1

    if not records:
        logger.warning("出力レコードが 0 件です。入力を確認してください。")
        return 1

    logger.info("クレンジングが完了しました。出力先: %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
