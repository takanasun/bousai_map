#!/usr/bin/env python3
"""e-Stat 国勢調査の人口メッシュ CSV をクレンジングし、地図描画用 JSON に変換する。

`KEY_CODE`（メッシュコード）には緯度経度が含まれていないため、
JIS X 0410（標準地域メッシュ）の規格に基づき四隅の座標を数学的に逆算する。

解像度は KEY_CODE の桁数で決まる（データ側が持つ情報であり、
1km データから 500m を作り出すことはできない）:

     8桁 … 3次メッシュ  約1km   （緯度30″×経度45″）
     9桁 … 4次メッシュ  約500m  （3次を 2×2 分割）
    10桁 … 5次メッシュ  約250m  （4次をさらに 2×2 分割）

    使い方:
        python scripts/clean_mesh.py                        # 通常実行
        python scripts/clean_mesh.py --mesh-prefix ""       # 県全域（絞り込みなし）
        python scripts/clean_mesh.py --input data/raw/population/mesh500.csv

出力先は解像度に応じて自動で決まる:
    population_mesh_1km.json / population_mesh_500m.json / population_mesh_250m.json

出力スキーマ (docs/spec.md 「4.1. 人口密度データ」準拠, 1要素):
    {
      "meshId": "53393581",
      "coordinates": [[lng, lat], [lng, lat], [lng, lat], [lng, lat], [lng, lat]],
      "populationDensity": 8500
    }

e-Stat CSV に固有の「罠」と対策:
    1. 2行目が日本語の副ヘッダ         → `skiprows=[1]` で強制スキップ
    2. 秘匿値が `-` や `X` で入る       → `to_numeric(errors="coerce")` → 0 で補完
    3. KEY_CODE が数値化され頭0が消える → `dtype={"KEY_CODE": str}` で文字列固定
    4. 文字コードが Shift-JIS(cp932)   → `encoding="cp932"` を既定に

備考:
    * 本スクリプトはビルド時のデータ加工用であり、Azure Functions には
      デプロイされない（.funcignore で scripts/ を除外済み）。そのため
      pandas への依存は requirements-dev.txt 側に置いている。
    * セル内人口をそのまま人口密度として扱う。1kmメッシュはほぼ 1km² なので
      人口＝人/km² だが、500m/250m ではセル面積が小さくなるため、
      密度に揃えたい場合は `--normalize-density` を使う。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("clean_mesh")

# --- 定数 ------------------------------------------------------------------

# プロジェクトルート (このファイルは <root>/scripts/clean_mesh.py にある)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 出力ディレクトリ。ファイル名は解像度に応じて決まる。
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "processed")

# 生データの置き場所は整理途中で揺れている（拡張子 .txt/.csv、種別サブ
# ディレクトリの有無）ため、既知の候補を順に探して最初に見つかったものを使う。
_INPUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "population")

_INPUT_CANDIDATES = (
    os.path.join(_INPUT_DIR, "kanagawa_pref_population_1km.csv"),
    os.path.join(_INPUT_DIR, "kanagawa_pref_population.csv"),
    os.path.join(PROJECT_ROOT, "data", "raw", "kanagawa_pref_population.csv"),
    os.path.join(PROJECT_ROOT, "data", "raw", "kanagawa_pref_population.txt"),
)


def _resolve_default_input() -> str:
    """既定の入力ファイルを候補から解決する（見つからなければ先頭候補を返す）。"""
    for candidate in _INPUT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return _INPUT_CANDIDATES[0]


DEFAULT_INPUT = _resolve_default_input()


def discover_input_files(input_dir: str = _INPUT_DIR) -> List[str]:
    """人口メッシュ CSV を探して返す（--all 用）。

    ファイル名の "500m" 等は当てにせず、実際の KEY_CODE 桁数で解像度を
    判定する（名前と中身が食い違っている実例があったため）。
    """
    if not os.path.isdir(input_dir):
        return []
    return sorted(
        os.path.join(input_dir, name)
        for name in os.listdir(input_dir)
        if name.lower().endswith(".csv")
    )

# e-Stat 令和2年国勢調査 メッシュ統計の列名
KEY_CODE_COLUMN = "KEY_CODE"

# 人口（総数）の列名は解像度ごとに異なる（T001100001 / T001102001 / T001225001…）。
# 決め打ちすると別解像度のファイルで壊れるため、2行目の日本語副ヘッダから
# 「人口（総数）」の列を自動検出する。下記は検出に失敗した場合の保険。
FALLBACK_POPULATION_COLUMNS = ("T001100001", "T001102001", "T001225001")

# 副ヘッダがこの文字列と一致する列を人口総数とみなす
POPULATION_HEADER_LABEL = "人口（総数）"

# 既定は神奈川県全域（空文字 = 絞り込みなし）。
#
# 神奈川県のデータは 5339 / 5239 / 5238 の3つの1次メッシュにまたがる。
#   5339 … 横浜・川崎・相模原・厚木
#   5239 … 横須賀・鎌倉・藤沢・平塚・小田原（三浦半島と湘南）
#   5238 … 県西端
# 一時期 5339 のみに絞っていたが、県南部が丸ごと欠けるため全域に戻した。
# 特定エリアに絞りたい場合のみ --mesh-prefix 5339 のように指定する。
DEFAULT_MESH_PREFIX = ""

# 3次（1km）メッシュ1セルの大きさ: 緯度30秒 × 経度45秒
LAT_CELL_DEG = 30 / 3600   # = 1/120 度
LNG_CELL_DEG = 45 / 3600   # = 1/80 度

# KEY_CODE の桁数 → 解像度名。
# 8桁の3次メッシュ(1km)以降は、1桁増えるごとに親セルを 2×2 に分割する。
RESOLUTION_BY_LENGTH: Dict[int, str] = {
    8: "1km",
    9: "500m",
    10: "250m",
    11: "125m",
}

# 解像度 → 1セルのおおよその面積(km²)。密度換算に使う。
CELL_AREA_KM2: Dict[str, float] = {
    "1km": 1.0,
    "500m": 0.25,
    "250m": 0.0625,
    "125m": 0.015625,
}


# --- メッシュコード → 座標の逆算 --------------------------------------------

def resolution_of(meshcode: Any) -> str:
    """KEY_CODE の桁数から解像度名を返す。

    Raises:
        ValueError: 対応していない桁数の場合。
    """
    code = str(meshcode).strip()
    resolution = RESOLUTION_BY_LENGTH.get(len(code))
    if resolution is None:
        raise ValueError(
            f"対応していないメッシュコードの桁数です（8〜11桁のみ対応）: {meshcode!r}"
        )
    return resolution


def meshcode_to_bbox(meshcode: Any) -> List[List[float]]:
    """メッシュコードから四隅の緯度経度（ポリゴン）を計算する。

    JIS X 0410（標準地域メッシュ）の定義:
        1次メッシュ(上4桁)  緯度 40分(2/3度) × 経度 1度
            緯度 = p / 1.5           (p = 上2桁)
            経度 = u + 100           (u = 次の2桁)
        2次メッシュ(次の2桁) 1次を 8×8 分割 → 緯度 5分 × 経度 7.5分
        3次メッシュ(下2桁)   2次を 10×10 分割 → 緯度 30秒 × 経度 45秒
        4次以降(1桁ずつ)     親セルを 2×2 分割。区画番号は
                             1=南西 2=南東 3=北西 4=北東

    Args:
        meshcode: 8〜11桁のメッシュコード（str / int どちらでも可）。

    Returns:
        [[lng, lat], ...] 形式の座標配列。GeoJSON 互換となるよう
        左下 → 右下 → 右上 → 左上 → 左下 の順で閉じたリング（5点）を返す。

    Raises:
        ValueError: 桁数が不正、数字以外を含む、区分番号が規格の範囲外の場合。
    """
    meshcode_str = str(meshcode).strip()

    if not meshcode_str.isdigit():
        raise ValueError(f"KEY_CODE は数字のみである必要があります: {meshcode!r}")

    # 桁数チェック（対応外なら ValueError）
    resolution_of(meshcode_str)

    # 1次メッシュ (上4桁)
    p = int(meshcode_str[0:2])
    u = int(meshcode_str[2:4])

    # 2次メッシュ (次の2桁) — 1次を 8×8 分割するので 0-7 のみ有効
    q = int(meshcode_str[4])
    v = int(meshcode_str[5])

    # 3次メッシュ (下2桁) — 2次を 10×10 分割するので 0-9
    r = int(meshcode_str[6])
    w = int(meshcode_str[7])

    if q > 7 or v > 7:
        raise ValueError(
            f"2次メッシュの区分番号は 0-7 の範囲である必要があります: {meshcode_str!r}"
        )

    # 3次メッシュ（1km）の南西端とセルサイズ
    lat_south = p / 1.5 + q * (5 / 60) + r * (30 / 3600)
    lng_west = (u + 100) + v * (7.5 / 60) + w * (45 / 3600)
    lat_size = LAT_CELL_DEG
    lng_size = LNG_CELL_DEG

    # 4次以降: 親セルを 2×2 に分割していく
    for index, digit_char in enumerate(meshcode_str[8:], start=4):
        quadrant = int(digit_char)
        if not 1 <= quadrant <= 4:
            raise ValueError(
                f"{index}次メッシュの区画番号は 1-4 の範囲である必要があります: "
                f"{meshcode_str!r}"
            )
        lat_size /= 2
        lng_size /= 2
        # 1=南西 2=南東 3=北西 4=北東
        lat_south += ((quadrant - 1) // 2) * lat_size
        lng_west += ((quadrant - 1) % 2) * lng_size

    lat_north = lat_south + lat_size
    lng_east = lng_west + lng_size

    # GeoJSON等で使える四角形の座標配列 [[lng, lat], ...]
    # 左下 -> 右下 -> 右上 -> 左上 -> 左下（閉じる）
    return [
        [lng_west, lat_south],
        [lng_east, lat_south],
        [lng_east, lat_north],
        [lng_west, lat_north],
        [lng_west, lat_south],
    ]


def detect_population_column(input_path: str, encoding: str = "cp932") -> str:
    """人口（総数）の列名を2行目の日本語副ヘッダから自動検出する。

    列名は解像度ごとに異なる（T001100001 / T001102001 / T001225001 …）ため、
    決め打ちにすると別の解像度のファイルで壊れる。副ヘッダの
    「人口（総数）」に一致する列を採用し、見つからなければ既知の候補を試す。

    Raises:
        ValueError: 人口列を特定できない場合。
    """
    header = pd.read_csv(input_path, encoding=encoding, nrows=1, dtype=str)
    columns = list(header.columns)
    subheader = header.iloc[0].tolist()

    for column, label in zip(columns, subheader):
        if label is None:
            continue
        # 全角スペースを含むことがあるため正規化して比較する
        normalized = str(label).replace("　", "").strip()
        if normalized == POPULATION_HEADER_LABEL:
            return column

    for candidate in FALLBACK_POPULATION_COLUMNS:
        if candidate in columns:
            logger.warning(
                "副ヘッダから人口列を検出できず、既知の列名 %r を使用します", candidate
            )
            return candidate

    raise ValueError(
        f"人口（総数）の列を特定できません。検出された列: {columns[:8]}"
    )


def output_filename_for(resolution: str, output_dir: str = DEFAULT_OUTPUT_DIR) -> str:
    """解像度に応じた出力パスを返す。

    ファイル名は `src/services/infra.py` の MESH_RESOURCES と一致させること。
    """
    return os.path.join(output_dir, f"population_mesh_{resolution}.json")


# --- クレンジング本体 -------------------------------------------------------

def clean_mesh_data(
    input_path: str,
    output_path: Optional[str] = None,
    mesh_prefix: Optional[str] = DEFAULT_MESH_PREFIX,
    encoding: str = "cp932",
    normalize_density: bool = False,
) -> List[Dict[str, Any]]:
    """e-Stat の人口メッシュ CSV を読み込み、仕様準拠の JSON へ変換して保存する。

    Args:
        input_path: 入力 CSV（e-Stat 形式, 既定は Shift-JIS）。
        output_path: 出力 JSON のパス。None なら解像度から自動決定する。
        mesh_prefix: 先頭一致で絞り込む1次メッシュコード。空/None なら絞り込まない。
        encoding: 入力ファイルの文字コード。
        normalize_density: True ならセル面積で割って 人/km² に換算する。
                           False（既定）はセル内人口をそのまま入れる。

    Returns:
        書き出したレコードのリスト。

    Raises:
        FileNotFoundError: 入力ファイルが存在しない場合。
        ValueError: 想定した列が存在しない場合。
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_path}")

    # 人口列の名前は解像度ごとに異なるため副ヘッダから特定する
    population_column = detect_population_column(input_path, encoding)

    # --- 罠1対策: 2行目の日本語副ヘッダを安全にスキップして列名を確定させる ---
    # 1行目を列名として使い、2行目（日本語解説）は読み飛ばす。
    # 罠3対策: KEY_CODE が数値落ちして頭の 0 が消えるのを防ぐ。
    try:
        df = pd.read_csv(
            input_path,
            encoding=encoding,
            skiprows=[1],
            dtype={KEY_CODE_COLUMN: str},
        )
    except UnicodeDecodeError as exc:
        logger.error(
            "文字コードの解釈に失敗しました (encoding=%s)。"
            "--encoding utf-8 等を試してください: %s",
            encoding,
            exc,
        )
        raise
    except pd.errors.ParserError as exc:
        logger.error("CSV の解析に失敗しました: %s (%s)", input_path, exc)
        raise

    for column in (KEY_CODE_COLUMN, population_column):
        if column not in df.columns:
            raise ValueError(
                f"必要な列 {column!r} が見つかりません。"
                f"検出された列: {list(df.columns)[:8]}"
            )

    total_rows = len(df)

    # KEY_CODE の欠損・空白を除去（8/9/10桁のいずれかを受け付ける）
    df[KEY_CODE_COLUMN] = df[KEY_CODE_COLUMN].astype(str).str.strip()
    df = df[df[KEY_CODE_COLUMN].str.fullmatch(r"\d{8,11}", na=False)]
    dropped_invalid_code = total_rows - len(df)

    if df.empty:
        raise ValueError("有効な KEY_CODE を含む行がありません")

    # 解像度を判定（混在していれば最も多い桁数を採用し、他は除外する）
    lengths = df[KEY_CODE_COLUMN].str.len()
    dominant_length = int(lengths.mode().iloc[0])
    resolution = RESOLUTION_BY_LENGTH[dominant_length]
    mixed = int((lengths != dominant_length).sum())
    if mixed:
        logger.warning("桁数の異なるコードを %d 件除外しました", mixed)
        df = df[lengths == dominant_length]

    # --- 罠2対策: 秘匿値（-, X, 空欄）のクレンジング ---
    # 人口総数列を数値に変換。エラー（文字列）は NaN にし、0 人として補完する。
    population = pd.to_numeric(df[population_column], errors="coerce")
    concealed = int(population.isna().sum())
    df = df.assign(**{population_column: population.fillna(0).astype(int)})

    # --- エリア絞り込み（1次メッシュコードの先頭一致） ---
    before_filter = len(df)
    if mesh_prefix:
        df = df[df[KEY_CODE_COLUMN].str.startswith(mesh_prefix, na=False)]
    dropped_by_prefix = before_filter - len(df)

    # --- レコード生成 ---
    area_km2 = CELL_AREA_KM2[resolution]
    processed_features: List[Dict[str, Any]] = []
    skipped_codes = 0

    for _, row in df.iterrows():
        mesh_id = row[KEY_CODE_COLUMN]
        try:
            coordinates = meshcode_to_bbox(mesh_id)
        except ValueError as exc:
            # 万が一パースできないコードがあればスキップ（黙って捨てず記録する）
            logger.warning("メッシュコードを解釈できずスキップしました: %s", exc)
            skipped_codes += 1
            continue

        population_count = int(row[population_column])
        density = round(population_count / area_km2) if normalize_density else population_count

        # 座標は小数6桁に丸める。緯度経度の6桁目は約0.1m に相当し、
        # メッシュ境界の表現には十分。丸めないと 35.333333333333336 のような
        # 17桁が全セル分並び、125m では JSON が 20MB を超えてしまう。
        processed_features.append(
            {
                "meshId": mesh_id,
                "coordinates": [[round(lng, 6), round(lat, 6)] for lng, lat in coordinates],
                "populationDensity": density,
            }
        )

    # --- 保存 ---
    if output_path is None:
        output_path = output_filename_for(resolution)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fp:
            # 機械生成のデータファイルであり人が読むものではないため、
            # インデントを付けない（125m では容量が3割以上変わる）。
            json.dump(processed_features, fp, ensure_ascii=False, separators=(",", ":"))
    except OSError as exc:
        logger.error("出力ファイルの書き込みに失敗しました: %s (%s)", output_path, exc)
        raise

    # --- サマリ ---
    logger.info("解像度: %s (KEY_CODE %d桁)", resolution, dominant_length)
    logger.info("読み込み行数: %d", total_rows)
    if dropped_invalid_code:
        logger.info("KEY_CODE が不正で除外: %d 件", dropped_invalid_code)
    if concealed:
        logger.info("秘匿値・欠損を 0 人として補完: %d 件", concealed)
    if dropped_by_prefix:
        logger.warning(
            "メッシュ接頭辞 %r による絞り込みで %d 件を除外しました。"
            "県全域が必要な場合は --mesh-prefix \"\" を指定してください。",
            mesh_prefix,
            dropped_by_prefix,
        )
    if skipped_codes:
        logger.warning("コード解釈に失敗して除外: %d 件", skipped_codes)
    logger.info("出力レコード数: %d → %s", len(processed_features), output_path)

    return processed_features


# --- CLI --------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="e-Stat 人口メッシュ CSV を地図描画用 JSON にクレンジングする"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="入力 CSV のパス")
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"{_INPUT_DIR} 内の CSV をすべて処理する（解像度は自動判定）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="出力 JSON のパス（既定: 解像度から自動決定）",
    )
    parser.add_argument(
        "--mesh-prefix",
        default=DEFAULT_MESH_PREFIX,
        help='絞り込む1次メッシュコード（空文字 "" で絞り込みなし）',
    )
    parser.add_argument("--encoding", default="cp932", help="入力ファイルの文字コード")
    parser.add_argument(
        "--normalize-density",
        action="store_true",
        help="セル面積で割って 人/km² に換算する（500m/250m の比較用）",
    )
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出力する")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.all:
        inputs = discover_input_files()
        if not inputs:
            logger.error("入力 CSV が見つかりません: %s", _INPUT_DIR)
            return 1
        if args.output:
            logger.error("--all と --output は同時に指定できません（出力先は自動決定）")
            return 1
    else:
        inputs = [args.input]

    failures = 0
    for path in inputs:
        logger.info("e-Statデータのクレンジングを開始します: %s", path)
        try:
            records = clean_mesh_data(
                input_path=path,
                output_path=args.output,
                mesh_prefix=args.mesh_prefix,
                encoding=args.encoding,
                normalize_density=args.normalize_density,
            )
        except (FileNotFoundError, ValueError, UnicodeDecodeError, OSError) as exc:
            logger.error("クレンジングに失敗しました: %s", exc)
            failures += 1
            continue

        if not records:
            logger.warning("出力レコードが 0 件です: %s", path)
            failures += 1

    if failures:
        logger.error("%d 件のファイルで失敗しました", failures)
        return 1

    logger.info("クレンジングが完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
