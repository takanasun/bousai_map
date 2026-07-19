"""`scripts/clean_hospitals.py` の単体テスト。

厚労省「医療情報ネット」の2ファイルを突き合わせて、docs/spec.md 4.2 の
スキーマに変換する処理を検証する。

このデータ固有の事情:
  1. 全国データ（7,640施設）なので都道府県コードで神奈川(14)に絞る必要がある
  2. 施設情報と診療科目が別ファイル。ID で結合し、診療科目を配列に畳む
  3. UTF-8 BOM 付き
  4. `機関区分` は全件 "1"（病院）で、災害拠点病院の区分は**含まれない**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import clean_hospitals  # noqa: E402


FACILITY_CSV = (
    "ID,正式名称,略称,機関区分,都道府県コード,市区町村コード,所在地,"
    "所在地座標（緯度）,所在地座標（経度）,案内用ホームページアドレス\n"
    "1411010000001,川崎中央病院,川崎中央,1,14,130,神奈川県川崎市川崎区1-1,35.5231,139.7215,https://example.com\n"
    "1411010000002,港こころのクリニック,港こころ,1,14,130,神奈川県川崎市川崎区2-2,35.5250,139.7300,\n"
    "0111010000010,札幌医科大学附属病院,札医大,1,01,101,北海道札幌市中央区,43.0554,141.3468,\n"
    "1411010000003,座標なし病院,座標なし,1,14,130,神奈川県川崎市川崎区3-3,,,\n"
)

SPECIALITY_CSV = (
    "ID,診療科目コード,診療科目名,診療時間帯,月_診療開始時間,月_診療終了時間\n"
    "1411010000001,01001,内科,1,09:00,17:30\n"
    "1411010000001,01021,循環器内科,1,09:00,17:30\n"
    "1411010000001,01001,内科,2,18:00,20:00\n"   # 同一科の重複（時間帯違い）
    "1411010000002,01041,精神科,1,10:00,18:00\n"
    "0111010000010,01001,内科,1,09:00,17:00\n"   # 北海道（対象外）
)


@pytest.fixture
def raw_dir(tmp_path):
    d = tmp_path / "hospital"
    d.mkdir()
    (d / "01-1_hospital_facility_info.csv").write_text(FACILITY_CSV, encoding="utf-8-sig")
    (d / "01-2_hospital_speciality_hours.csv").write_text(SPECIALITY_CSV, encoding="utf-8-sig")
    return d


# --- ファイル種別の判定 ----------------------------------------------------- #

def test_detects_file_kind_from_columns():
    """ファイル名ではなく列で判定する（名前と中身がずれた前例があるため）。"""
    assert clean_hospitals.detect_kind(["ID", "正式名称", "所在地座標（緯度）"]) == clean_hospitals.KIND_FACILITY
    assert clean_hospitals.detect_kind(["ID", "診療科目名", "診療時間帯"]) == clean_hospitals.KIND_SPECIALITY


def test_unknown_columns_raise():
    with pytest.raises(ValueError):
        clean_hospitals.detect_kind(["ID", "なにか"])


# --- 都道府県の絞り込み ------------------------------------------------------ #

def test_filters_to_kanagawa_by_prefecture_code(raw_dir, tmp_path):
    """全国データから神奈川(14)のみを残す。"""
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    names = {r["name"] for r in records}
    assert "札幌医科大学附属病院" not in names
    assert "川崎中央病院" in names


def test_prefecture_code_is_configurable(raw_dir, tmp_path):
    records = clean_hospitals.clean_hospitals(
        str(raw_dir), str(tmp_path / "out.json"), prefecture_code="01"
    )
    assert [r["name"] for r in records] == ["札幌医科大学附属病院"]


# --- 診療科目の結合 ---------------------------------------------------------- #

def test_specialities_are_merged_into_capabilities(raw_dir, tmp_path):
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    by_name = {r["name"]: r for r in records}
    assert by_name["川崎中央病院"]["capabilities"] == ["内科", "循環器内科"]
    assert by_name["港こころのクリニック"]["capabilities"] == ["精神科"]


def test_duplicate_specialities_are_deduplicated(raw_dir, tmp_path):
    """同じ科が時間帯違いで複数行あっても1つに畳む。"""
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    caps = next(r for r in records if r["name"] == "川崎中央病院")["capabilities"]
    assert caps.count("内科") == 1


def test_facility_without_specialities_gets_empty_list(raw_dir, tmp_path):
    """診療科の行が無い施設でも capabilities は配列であること。"""
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    for r in records:
        assert isinstance(r["capabilities"], list)


# --- 座標 -------------------------------------------------------------------- #

def test_rows_without_coordinates_are_skipped(raw_dir, tmp_path):
    """地図に描けないため座標欠損は除外する。"""
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    assert "座標なし病院" not in {r["name"] for r in records}


def test_coordinates_are_floats_in_japan_range(raw_dir, tmp_path):
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    for r in records:
        assert isinstance(r["location"]["lat"], float)
        assert 20.0 <= r["location"]["lat"] <= 46.0
        assert 122.0 <= r["location"]["lng"] <= 154.0


# --- 出力スキーマ ------------------------------------------------------------ #

def test_output_schema(raw_dir, tmp_path):
    out = tmp_path / "nested" / "hospitals.json"
    clean_hospitals.clean_hospitals(str(raw_dir), str(out))

    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    record = next(r for r in saved if r["name"] == "川崎中央病院")

    assert set(record) == {
        "id", "name", "address", "location",
        "isDisasterBase", "capabilities", "topDiseases", "website",
    }
    assert record["id"].startswith("hosp_")


def test_disaster_base_defaults_to_false(raw_dir, tmp_path):
    """`機関区分` は全件 "1"(病院) で災害拠点病院の情報が無いため false 固定。

    別データ（自治体の災害拠点病院一覧）を取り込むまで true にはならない。
    """
    records = clean_hospitals.clean_hospitals(str(raw_dir), str(tmp_path / "out.json"))
    assert all(r["isDisasterBase"] is False for r in records)


def test_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        clean_hospitals.clean_hospitals(str(tmp_path / "nope"), str(tmp_path / "o.json"))
