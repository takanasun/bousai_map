"""`scripts/clean_mesh.py` の単体テスト。

検証の柱:
  1. KEY_CODE(8桁 3次メッシュ)から緯度経度の矩形を逆算する数学ロジック
  2. e-Stat CSV 特有の「罠」の処理
     - 2行目の日本語副ヘッダをスキップできているか
     - 秘匿値(`-`, `X`, 空欄)を 0 にクレンジングできているか
     - KEY_CODE が数値化されず文字列のまま保持されているか

数学ロジックの拠り所(公表されている既知の値):
  * 第1次地域区画メッシュ "5339" の南西端は 北緯35°20′00″(=35.33333…°),
    東経139°00′00″。(総務省 標準地域メッシュ / JIS X 0410)
  * 3次(1km)メッシュのセルは 緯度 30″(=1/120度), 経度 45″(=1/80度)。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# scripts/ を import パスに追加（pytest.ini の pythonpath はプロジェクトルートのみ）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import clean_mesh  # noqa: E402


# ---------------------------------------------------------------------------
# セルサイズ（3次メッシュ = 1km メッシュ）
# ---------------------------------------------------------------------------

LAT_CELL = 30 / 3600   # 30 秒 = 1/120 度
LNG_CELL = 45 / 3600   # 45 秒 = 1/80 度


def _bbox_extent(bbox):
    """bbox(座標配列)から (西端, 南端, 東端, 北端) を取り出す。"""
    lngs = [p[0] for p in bbox]
    lats = [p[1] for p in bbox]
    return min(lngs), min(lats), max(lngs), max(lats)


# ---------------------------------------------------------------------------
# 1. メッシュコード → 矩形座標の逆算
# ---------------------------------------------------------------------------

def test_bbox_is_closed_ring_of_five_points():
    """GeoJSON 互換の閉じたリング(始点と終点が一致する5点)を返す。"""
    bbox = clean_mesh.meshcode_to_bbox("53393581")
    assert len(bbox) == 5
    assert bbox[0] == bbox[-1]


def test_bbox_coordinates_are_lng_lat_order():
    """仕様 4.1 の coordinates は [lng, lat] の順（経度が先）。"""
    bbox = clean_mesh.meshcode_to_bbox("53393581")
    for lng, lat in bbox:
        # 日本国内の経度は 3桁台、緯度は 2桁台。取り違えると範囲で弾ける。
        assert 122.0 <= lng <= 154.0
        assert 20.0 <= lat <= 46.0


def test_southwest_corner_of_first_cell_in_mesh_5339():
    """"53390000" は1次メッシュ5339の南西端そのもの(北緯35°20′/東経139°00′)。"""
    bbox = clean_mesh.meshcode_to_bbox("53390000")
    west, south, east, north = _bbox_extent(bbox)
    assert south == pytest.approx(35.333333, abs=1e-6)   # 35°20′N
    assert west == pytest.approx(139.0, abs=1e-9)        # 139°00′E
    assert north == pytest.approx(35.333333 + LAT_CELL, abs=1e-6)
    assert east == pytest.approx(139.0 + LNG_CELL, abs=1e-9)


def test_southwest_corner_of_spec_example_53393581():
    """仕様書 4.1 の例。手計算:
        緯度 = 53/1.5 + 3*(5/60) + 8*(30/3600) = 35.33333 + 0.25 + 0.06667 = 35.65
        経度 = 139   + 5*(7.5/60) + 1*(45/3600) = 139 + 0.625 + 0.0125    = 139.6375
    """
    bbox = clean_mesh.meshcode_to_bbox("53393581")
    west, south, east, north = _bbox_extent(bbox)
    assert south == pytest.approx(35.65, abs=1e-6)
    assert west == pytest.approx(139.6375, abs=1e-6)
    assert north == pytest.approx(35.65 + LAT_CELL, abs=1e-6)
    assert east == pytest.approx(139.6375 + LNG_CELL, abs=1e-6)


def test_cell_size_is_30sec_by_45sec():
    """どのメッシュでもセルサイズは 緯度30″×経度45″ で一定。"""
    for code in ("53390000", "53393581", "52386789"):
        west, south, east, north = _bbox_extent(clean_mesh.meshcode_to_bbox(code))
        assert (north - south) == pytest.approx(LAT_CELL, abs=1e-9)
        assert (east - west) == pytest.approx(LNG_CELL, abs=1e-9)


def test_cell_area_is_about_1km2():
    """3次メッシュは「1kmメッシュ」。緯度35度付近で概ね 0.7〜1.1 km²。"""
    west, south, east, north = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581"))
    width_km = (east - west) * 111.320 * math.cos(math.radians(south))
    height_km = (north - south) * 110.574
    assert 0.7 <= width_km * height_km <= 1.1


def test_real_key_code_from_kanagawa_falls_inside_prefecture():
    """実ファイル先頭の実コード。神奈川県の緯度経度レンジに収まること。"""
    west, south, east, north = _bbox_extent(clean_mesh.meshcode_to_bbox("52386789"))
    assert 35.0 <= south <= 35.7
    assert 138.9 <= west <= 139.8


def test_adjacent_codes_are_adjacent_cells():
    """末尾+1 は東隣のセル。西端が1セル分だけ東へずれる。"""
    a_w, a_s, _, _ = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581"))
    b_w, b_s, _, _ = _bbox_extent(clean_mesh.meshcode_to_bbox("53393582"))
    assert b_w - a_w == pytest.approx(LNG_CELL, abs=1e-9)
    assert b_s == pytest.approx(a_s, abs=1e-9)


def test_bbox_accepts_int_code():
    """CSV 由来で int が渡っても文字列化して処理する。"""
    assert clean_mesh.meshcode_to_bbox(53393581) == clean_mesh.meshcode_to_bbox("53393581")


# ---------------------------------------------------------------------------
# 2. e-Stat CSV のクレンジング
# ---------------------------------------------------------------------------

# 実ファイル(kanagawa_pref_population.txt)と同じ構造を最小限で再現したモック。
#   1行目: 英字の列名 / 2行目: 日本語の副ヘッダ / 3行目以降: データ
MOCK_CSV = (
    "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T001100001,T001100002\n"
    "　,　,　,　,　人口（総数）,　人口（総数）　男\n"
    "53393581,0,,,8500,4200\n"      # 通常行
    "53393582,0,,,3200,1600\n"      # 通常行
    "53393591,1,,,-,-\n"            # 秘匿値(ハイフン)
    "53393592,1,,,X,X\n"            # 秘匿値(X)
    "53393593,0,,,,\n"              # 空欄
    "52386789,0,,,90,49\n"          # 1次メッシュ 5238（対象エリア外）
)


@pytest.fixture
def mock_csv(tmp_path):
    """e-Stat 形式(cp932)のモック CSV を書き出してパスを返す。"""
    path = tmp_path / "mock_mesh.csv"
    path.write_text(MOCK_CSV, encoding="cp932")
    return path


def test_skips_japanese_subheader_row(mock_csv, tmp_path):
    """2行目の日本語副ヘッダがデータとして混入しないこと。"""
    out = tmp_path / "out.json"
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="")
    mesh_ids = [r["meshId"] for r in records]
    # 副ヘッダ由来の全角スペース等が meshId に混ざっていない
    assert all(mid.isdigit() and len(mid) == 8 for mid in mesh_ids)
    assert "　" not in "".join(mesh_ids)


def test_confidential_values_become_zero(mock_csv, tmp_path):
    """秘匿値(`-`, `X`, 空欄)は 0 人として扱う。"""
    out = tmp_path / "out.json"
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="")
    density = {r["meshId"]: r["populationDensity"] for r in records}
    assert density["53393591"] == 0   # "-"
    assert density["53393592"] == 0   # "X"
    assert density["53393593"] == 0   # 空欄
    # 通常値は保持される
    assert density["53393581"] == 8500
    assert density["53393582"] == 3200


def test_population_is_int_not_float(mock_csv, tmp_path):
    """人口は int。float("8500.0") になっていないこと。"""
    out = tmp_path / "out.json"
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="")
    assert all(isinstance(r["populationDensity"], int) for r in records)


def test_mesh_id_stays_string(mock_csv, tmp_path):
    """KEY_CODE が数値化(53393581.0)されず文字列のままであること。"""
    out = tmp_path / "out.json"
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="")
    assert all(isinstance(r["meshId"], str) for r in records)
    assert all("." not in r["meshId"] for r in records)


def test_mesh_prefix_filter(mock_csv, tmp_path):
    """mesh_prefix でエリアを絞り込める（5238 は除外される）。"""
    out = tmp_path / "out.json"
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="5339")
    mesh_ids = {r["meshId"] for r in records}
    assert "52386789" not in mesh_ids
    assert all(mid.startswith("5339") for mid in mesh_ids)
    assert len(records) == 5


def test_empty_prefix_keeps_all_rows(mock_csv, tmp_path):
    """mesh_prefix を空にすると絞り込みなし（全6行）。"""
    out = tmp_path / "out.json"
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="")
    assert len(records) == 6


def test_output_matches_spec_schema(mock_csv, tmp_path):
    """出力 JSON が docs/spec.md 4.1 のスキーマ通りであること。"""
    out = tmp_path / "nested" / "population_mesh.json"
    clean_mesh.clean_mesh_data(str(mock_csv), str(out), mesh_prefix="5339")

    assert out.exists()  # 出力先ディレクトリが自動生成される
    with open(out, encoding="utf-8") as fp:
        saved = json.load(fp)

    assert isinstance(saved, list) and saved
    record = saved[0]
    assert set(record) == {"meshId", "coordinates", "populationDensity"}
    assert record["meshId"] == "53393581"
    assert record["populationDensity"] == 8500
    # 出力座標は小数6桁に丸められる（容量削減のため。約0.1m 精度）
    expected = [[round(lng, 6), round(lat, 6)] for lng, lat in clean_mesh.meshcode_to_bbox("53393581")]
    assert record["coordinates"] == expected


def test_missing_input_file_raises(tmp_path):
    """入力ファイルが無い場合は握り潰さずエラーにする。"""
    with pytest.raises(FileNotFoundError):
        clean_mesh.clean_mesh_data(
            str(tmp_path / "nope.csv"), str(tmp_path / "out.json")
        )


# ---------------------------------------------------------------------------
# 3. 解像度（メッシュ次数）の切り替え
#
#    KEY_CODE の桁数が解像度を表す:
#      8桁 = 3次メッシュ(1km) / 9桁 = 4次(500m) / 10桁 = 5次(250m)
#    4次以降は親セルを 2x2 に分割し、区画番号は 1=南西 2=南東 3=北西 4=北東。
# ---------------------------------------------------------------------------

def test_resolution_detected_from_code_length():
    assert clean_mesh.resolution_of("53393581") == "1km"
    assert clean_mesh.resolution_of("533935811") == "500m"
    assert clean_mesh.resolution_of("5339358111") == "250m"
    assert clean_mesh.resolution_of("53393581111") == "125m"


def test_unsupported_code_length_raises():
    # 7桁以下・12桁以上は規格外
    for bad in ("1234567", "123456789012"):
        with pytest.raises(ValueError):
            clean_mesh.resolution_of(bad)


def test_125m_cell_is_one_eighth_of_1km_cell():
    west, south, east, north = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581111"))
    assert (north - south) == pytest.approx(LAT_CELL / 8, abs=1e-9)
    assert (east - west) == pytest.approx(LNG_CELL / 8, abs=1e-9)


def test_500m_cell_is_quarter_of_1km_cell():
    """500mセルは1kmセルの1/2×1/2。"""
    west, south, east, north = _bbox_extent(clean_mesh.meshcode_to_bbox("533935811"))
    assert (north - south) == pytest.approx(LAT_CELL / 2, abs=1e-9)
    assert (east - west) == pytest.approx(LNG_CELL / 2, abs=1e-9)


def test_250m_cell_is_sixteenth_of_1km_cell():
    west, south, east, north = _bbox_extent(clean_mesh.meshcode_to_bbox("5339358111"))
    assert (north - south) == pytest.approx(LAT_CELL / 4, abs=1e-9)
    assert (east - west) == pytest.approx(LNG_CELL / 4, abs=1e-9)


def test_500m_quadrant_numbering():
    """区画番号 1=南西 2=南東 3=北西 4=北東 の並びになっていること。"""
    parent_w, parent_s, _, _ = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581"))
    half_lat, half_lng = LAT_CELL / 2, LNG_CELL / 2

    expected = {
        "1": (parent_w, parent_s),                          # 南西
        "2": (parent_w + half_lng, parent_s),               # 南東
        "3": (parent_w, parent_s + half_lat),               # 北西
        "4": (parent_w + half_lng, parent_s + half_lat),    # 北東
    }
    for digit, (exp_w, exp_s) in expected.items():
        west, south, _, _ = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581" + digit))
        assert west == pytest.approx(exp_w, abs=1e-9), f"区画{digit}の西端"
        assert south == pytest.approx(exp_s, abs=1e-9), f"区画{digit}の南端"


def test_child_cells_are_contained_in_parent():
    """4つの子セルは必ず親セルの内側に収まる。

    親と子では緯度の加算順序が異なるため IEEE754 の丸め誤差が乗る。
    1e-9度（約0.1mm）を許容誤差とする。
    """
    eps = 1e-9
    p_w, p_s, p_e, p_n = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581"))
    for digit in "1234":
        w, s, e, n = _bbox_extent(clean_mesh.meshcode_to_bbox("53393581" + digit))
        assert p_w - eps <= w and e <= p_e + eps
        assert p_s - eps <= s and n <= p_n + eps


def test_invalid_quadrant_digit_raises():
    """区画番号は 1-4。0 や 5 は不正。"""
    for bad in ("533935810", "533935815"):
        with pytest.raises(ValueError):
            clean_mesh.meshcode_to_bbox(bad)


def test_output_filename_includes_resolution():
    assert clean_mesh.output_filename_for("1km").endswith("population_mesh_1km.json")
    assert clean_mesh.output_filename_for("500m").endswith("population_mesh_500m.json")


def test_clean_mesh_data_writes_resolution_suffixed_file(tmp_path):
    """解像度に応じたファイル名で出力されること。"""
    csv = tmp_path / "mesh500.csv"
    csv.write_text(
        "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T001100001\n"
        "　,　,　,　,　人口（総数）\n"
        "533935811,0,,,4200\n"
        "533935812,0,,,3100\n",
        encoding="cp932",
    )
    records = clean_mesh.clean_mesh_data(str(csv), str(tmp_path / "out.json"), mesh_prefix="")
    assert len(records) == 2
    # 500m セルであること（座標は6桁丸めのため許容誤差は 1e-6）
    ring = records[0]["coordinates"]
    lats = [p[1] for p in ring]
    assert (max(lats) - min(lats)) == pytest.approx(LAT_CELL / 2, abs=1e-6)


def test_output_coordinates_are_rounded_to_six_decimals(mock_csv, tmp_path):
    """座標は小数6桁に丸めて出力する。

    丸めないと 35.333333333333336 のような17桁が全セル分並び、
    125m メッシュでは JSON が 20MB を超えて実用にならない。
    6桁は約0.1m 相当でメッシュ境界の表現には十分。
    """
    records = clean_mesh.clean_mesh_data(str(mock_csv), str(tmp_path / "out.json"), mesh_prefix="")
    for record in records:
        for lng, lat in record["coordinates"]:
            assert round(lng, 6) == lng
            assert round(lat, 6) == lat
