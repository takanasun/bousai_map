#!/usr/bin/env python3
"""厚労省「医療情報ネット」の医療機関データをクレンジングして JSON に変換する。

施設の基本情報（01-1）と診療科目・診療時間（01-2）が別ファイルに分かれており、
`ID` で結合して1施設1レコードに畳む。全国データのため都道府県コードで絞り込む。

    使い方:
        python scripts/clean_hospitals.py
        python scripts/clean_hospitals.py --prefecture-code 13   # 東京都
        python scripts/clean_hospitals.py --input-dir data/raw/hospital

出力スキーマ (docs/spec.md 「4.2. 医療機関データ」準拠, 1要素):
    {
      "id": "hosp_1411010000001",
      "name": "川崎中央病院",
      "address": "神奈川県川崎市川崎区1-1",
      "location": { "lat": 35.5231, "lng": 139.7215 },
      "isDisasterBase": false,
      "capabilities": ["内科", "循環器内科"],
      "topDiseases": [],
      "website": "https://example.com"
    }

このデータ固有の注意点:
    * ファイル名ではなく **列の顔ぶれ** で種別を判定する（避難所データで
      名前と中身が入れ違っていた前例があるため）。
    * `機関区分` は全件 "1"（病院）で、**災害拠点病院の区分は含まれない**。
      そのため `isDisasterBase` は false 固定。仕様 5.3 の「災害拠点病院」
      表示を実現するには、自治体が公開する災害拠点病院一覧を別途取り込む必要がある。
    * `topDiseases`（強みのある疾患）もこのデータには無いため空配列。
    * UTF-8 BOM 付き。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger("clean_hospitals")

# --- 定数 ------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_INPUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "hospital")
# 出力ファイル名は `src/services/infra.py` の DATASET_RESOURCES["hospitals"] と一致させる
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "data", "processed", "hospitals.json")

# 神奈川県の都道府県コード
DEFAULT_PREFECTURE_CODE = "14"

KIND_FACILITY = "facility"      # 01-1 施設基本情報
KIND_SPECIALITY = "speciality"  # 01-2 診療科目・診療時間

ID_COLUMN = "ID"
NAME_COLUMN = "正式名称"
ADDRESS_COLUMN = "所在地"
LAT_COLUMN = "所在地座標（緯度）"
LNG_COLUMN = "所在地座標（経度）"
PREFECTURE_COLUMN = "都道府県コード"
WEBSITE_COLUMN = "案内用ホームページアドレス"
SPECIALITY_COLUMN = "診療科目名"


# --- ファイル種別の判定 ------------------------------------------------------

def detect_kind(columns: Iterable[str]) -> str:
    """列の顔ぶれからファイル種別を判定する。

    ファイル名は当てにしない（避難所データで名前と中身が入れ違っていたため）。

    Raises:
        ValueError: どちらの形式とも判定できない場合。
    """
    column_set = set(columns)

    if SPECIALITY_COLUMN in column_set:
        return KIND_SPECIALITY
    if LAT_COLUMN in column_set or NAME_COLUMN in column_set:
        return KIND_FACILITY

    raise ValueError(
        "医療機関データの種別を判定できません。"
        f"検出された列: {sorted(column_set)[:10]}"
    )


# --- 値の正規化 --------------------------------------------------------------

def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in ("nan", "None") else text


def _parse_coordinate(value: Any) -> Optional[float]:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if -180.0 <= number <= 180.0 else None


def _iter_csv_files(input_dir: str) -> List[str]:
    return sorted(
        os.path.join(input_dir, name)
        for name in os.listdir(input_dir)
        if name.lower().endswith(".csv")
    )


# --- クレンジング本体 --------------------------------------------------------

def clean_hospitals(
    input_dir: str,
    output_path: str,
    prefecture_code: str = DEFAULT_PREFECTURE_CODE,
    encoding: str = "utf-8-sig",
) -> List[Dict[str, Any]]:
    """医療機関 CSV 群を読み込み、統合した JSON を保存する。

    Args:
        input_dir: 01-1 / 01-2 の CSV を含むディレクトリ。
        output_path: 出力 JSON のパス。
        prefecture_code: 絞り込む都道府県コード（"14" = 神奈川県）。
        encoding: 入力ファイルの文字コード。

    Returns:
        統合後のレコード一覧。

    Raises:
        FileNotFoundError: 入力ディレクトリまたは CSV が存在しない場合。
        ValueError: 施設情報ファイルが見つからない場合。
    """
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"入力ディレクトリが見つかりません: {input_dir}")

    csv_paths = _iter_csv_files(input_dir)
    if not csv_paths:
        raise FileNotFoundError(f"CSV が1件も見つかりません: {input_dir}")

    facilities: Optional[pd.DataFrame] = None
    specialities: Optional[pd.DataFrame] = None

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
            os.path.basename(path),
            len(df),
            "施設情報" if kind == KIND_FACILITY else "診療科目",
        )
        if kind == KIND_FACILITY:
            facilities = df
        else:
            specialities = df

    if facilities is None:
        raise ValueError(f"施設情報ファイルが見つかりません: {input_dir}")

    total = len(facilities)

    # --- 都道府県で絞り込み ---
    if prefecture_code and PREFECTURE_COLUMN in facilities.columns:
        facilities = facilities[
            facilities[PREFECTURE_COLUMN].astype(str).str.strip() == prefecture_code
        ]
    dropped_by_prefecture = total - len(facilities)

    # --- 診療科目を ID ごとに畳む（重複排除・出現順を保持） ---
    capabilities_by_id: Dict[str, List[str]] = {}
    if specialities is not None:
        for row in specialities.to_dict("records"):
            facility_id = _clean_text(row.get(ID_COLUMN))
            speciality = _clean_text(row.get(SPECIALITY_COLUMN))
            if not facility_id or not speciality:
                continue
            bucket = capabilities_by_id.setdefault(facility_id, [])
            if speciality not in bucket:
                bucket.append(speciality)

    # --- レコード生成 ---
    records: List[Dict[str, Any]] = []
    skipped_no_coords = 0

    for row in facilities.to_dict("records"):
        facility_id = _clean_text(row.get(ID_COLUMN))
        name = _clean_text(row.get(NAME_COLUMN))
        lat = _parse_coordinate(row.get(LAT_COLUMN))
        lng = _parse_coordinate(row.get(LNG_COLUMN))

        if not name or lat is None or lng is None:
            skipped_no_coords += 1
            continue

        records.append(
            {
                "id": f"hosp_{facility_id}",
                "name": name,
                "address": _clean_text(row.get(ADDRESS_COLUMN)),
                "location": {"lat": lat, "lng": lng},
                # このデータには災害拠点病院の区分が無いため false 固定。
                # 自治体の災害拠点病院一覧を取り込むまで true にはならない。
                "isDisasterBase": False,
                "capabilities": capabilities_by_id.get(facility_id, []),
                # 「強みのある疾患」もこのデータには含まれない
                "topDiseases": [],
                "website": _clean_text(row.get(WEBSITE_COLUMN)),
            }
        )

    # --- 保存 ---
    out_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(records, fp, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("出力ファイルの書き込みに失敗しました: %s (%s)", output_path, exc)
        raise

    logger.info("全国施設数: %d", total)
    if dropped_by_prefecture:
        logger.info("都道府県コード %s 以外を除外: %d 件", prefecture_code, dropped_by_prefecture)
    if skipped_no_coords:
        logger.info("座標・名称の欠損で除外: %d 件", skipped_no_coords)
    logger.info(
        "診療科あり: %d 件 / 診療科なし: %d 件",
        sum(1 for r in records if r["capabilities"]),
        sum(1 for r in records if not r["capabilities"]),
    )
    logger.info("出力レコード数: %d → %s", len(records), output_path)

    return records


# --- CLI --------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="厚労省 医療情報ネットの医療機関 CSV を地図描画用 JSON に統合する"
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR, help="医療機関 CSV のディレクトリ")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="出力 JSON のパス")
    parser.add_argument(
        "--prefecture-code",
        default=DEFAULT_PREFECTURE_CODE,
        help='絞り込む都道府県コード（"14"=神奈川。空文字で全国）',
    )
    parser.add_argument("--encoding", default="utf-8-sig", help="入力ファイルの文字コード")
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出力する")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info("医療機関データのクレンジングを開始します: %s", args.input_dir)
    try:
        records = clean_hospitals(
            input_dir=args.input_dir,
            output_path=args.output,
            prefecture_code=args.prefecture_code,
            encoding=args.encoding,
        )
    except (FileNotFoundError, ValueError, UnicodeDecodeError, OSError) as exc:
        logger.error("クレンジングに失敗しました: %s", exc)
        return 1

    if not records:
        logger.warning("出力レコードが 0 件です。--prefecture-code を確認してください。")
        return 1

    logger.info("クレンジングが完了しました。出力先: %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
