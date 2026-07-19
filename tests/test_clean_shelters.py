"""`scripts/clean_shelters.py` の単体テスト。

避難所には性質の異なる2種類があり、災害時の意味がまったく違う。
    * 指定緊急避難場所 … 切迫した危険から「命を守る」ために逃げ込む場所。
                        災害種別（洪水/地震/津波…）ごとに指定される。
    * 指定避難所       … 自宅に戻れない人が一定期間「生活する」場所。
                        災害種別の指定は無く、受入対象者の定めがある。
両者は同一施設が兼ねることもあるため、施設名＋住所で名寄せして統合する。

国土地理院データ固有の「罠」:
    1. ディレクトリ名と中身が入れ違っている
       → 列の顔ぶれで種別を判定する（ディレクトリ名を信用しない）
    2. 災害種別は該当時のみ "1"、非該当は空欄
    3. UTF-8 BOM 付き
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ を import パスに追加（pytest.ini の pythonpath はプロジェクトルートのみ）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import clean_shelters  # noqa: E402


# ---------------------------------------------------------------------------
# モック CSV（実ファイルと同じ列構成・BOM付き UTF-8）
# ---------------------------------------------------------------------------

EMERGENCY_CSV = (
    "NO,共通ID,施設・場所名,住所,洪水,崖崩れ、土石流及び地滑り,高潮,地震,津波,"
    "大規模な火事,内水氾濫,火山現象,指定避難所との住所同一,緯度,経度,備考\n"
    "1,E1413000000001,川崎中学校,神奈川県横浜市中区日本大通1,1,,,1,,1,,,1,35.5231,139.7215,\n"
    "2,E1413000000002,港町公園,神奈川県川崎市川崎区港町2-2,,,,1,1,,,,,35.5250,139.7300,\n"
    "3,E1413000000003,高台広場,神奈川県川崎市川崎区高台3-3,,1,,,,,,1,,35.5300,139.7350,\n"
)

CENTER_CSV = (
    "NO,共通ID,施設・場所名,住所,指定緊急避難場所との住所同一,"
    "その他市町村長が必要と認める事項,受入対象者,緯度,経度,備考\n"
    # 1件目は EMERGENCY_CSV と同一施設（名寄せ対象）
    "1,E1413000001001,川崎中学校,神奈川県横浜市中区日本大通1,1,,,35.5231,139.7215,\n"
    "2,E1413000001002,さくら福祉センター,神奈川県川崎市川崎区桜町4-4,,,要配慮者及びその家族,35.5280,139.7250,\n"
)


@pytest.fixture
def raw_dir(tmp_path):
    """実ファイルと同じ「入れ違った」ディレクトリ名でモックを配置する。"""
    site_dir = tmp_path / "designated_emergency_evacuation_site"
    center_dir = tmp_path / "designated_evacuation_center"
    site_dir.mkdir()
    center_dir.mkdir()
    # 実データと同様に、名前と中身をわざと入れ違いにする
    (site_dir / "kanagawa_pref.csv").write_text(CENTER_CSV, encoding="utf-8-sig")
    (center_dir / "kanagawa_pref.csv").write_text(EMERGENCY_CSV, encoding="utf-8-sig")
    return tmp_path


# ---------------------------------------------------------------------------
# 種別の判定（ディレクトリ名を信用しない）
# ---------------------------------------------------------------------------

def test_detects_dataset_kind_from_columns():
    """列の顔ぶれから種別を判定する。"""
    emergency_cols = ["NO", "共通ID", "施設・場所名", "住所", "洪水", "地震", "緯度", "経度"]
    center_cols = ["NO", "共通ID", "施設・場所名", "住所", "受入対象者", "緯度", "経度"]
    assert clean_shelters.detect_kind(emergency_cols) == clean_shelters.KIND_EMERGENCY
    assert clean_shelters.detect_kind(center_cols) == clean_shelters.KIND_CENTER


def test_unknown_columns_raise():
    with pytest.raises(ValueError):
        clean_shelters.detect_kind(["NO", "なにか", "緯度", "経度"])


# ---------------------------------------------------------------------------
# 災害種別
# ---------------------------------------------------------------------------

def test_disaster_types_extracted_from_flag_columns():
    row = {"洪水": "1", "地震": "1", "大規模な火事": "1", "津波": None, "火山現象": ""}
    assert clean_shelters.extract_disaster_types(row) == ["flood", "earthquake", "fire"]


def test_disaster_types_empty_when_none_flagged():
    assert clean_shelters.extract_disaster_types({"洪水": "", "地震": None}) == []


def test_disaster_type_keys_cover_all_eight_categories():
    """災害対策基本法の8種別すべてに対応キーがあること。"""
    assert len(clean_shelters.DISASTER_COLUMNS) == 8
    assert set(clean_shelters.DISASTER_COLUMNS.values()) == {
        "flood", "landslide", "stormSurge", "earthquake",
        "tsunami", "fire", "inlandFlood", "volcano",
    }


# ---------------------------------------------------------------------------
# 福祉避難所の判定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "要配慮者及びその家族",
    "災害時において、高齢者、障害者、乳幼児、その他の特に配慮を要する者",
    "市が特定した障害者等",
])
def test_welfare_shelter_detected_from_target_occupants(text):
    assert clean_shelters.is_welfare_shelter(text) is True


@pytest.mark.parametrize("text", ["", None, "全住民"])
def test_not_welfare_shelter(text):
    assert clean_shelters.is_welfare_shelter(text) is False


# ---------------------------------------------------------------------------
# 統合処理
# ---------------------------------------------------------------------------

def test_merges_same_facility_across_both_datasets(raw_dir, tmp_path):
    """同一施設（施設名＋住所が一致）は1件に統合し、両方の役割を持たせる。"""
    out = tmp_path / "evacuation_sites.json"
    records = clean_shelters.clean_shelters(str(raw_dir), str(out))

    by_name = {r["name"]: r for r in records}
    kawasaki = by_name["川崎中学校"]
    assert kawasaki["isEmergencySite"] is True    # 命を守る場所
    assert kawasaki["isEvacuationCenter"] is True  # 生活する場所
    # 3ファイル分の重複が解消され、ユニーク施設数になる
    assert len(records) == 4


def test_emergency_only_facility(raw_dir, tmp_path):
    out = tmp_path / "out.json"
    records = clean_shelters.clean_shelters(str(raw_dir), str(out))
    park = next(r for r in records if r["name"] == "港町公園")
    assert park["isEmergencySite"] is True
    assert park["isEvacuationCenter"] is False
    assert park["disasterTypes"] == ["earthquake", "tsunami"]


def test_center_only_facility_keeps_welfare_flag(raw_dir, tmp_path):
    out = tmp_path / "out.json"
    records = clean_shelters.clean_shelters(str(raw_dir), str(out))
    welfare = next(r for r in records if r["name"] == "さくら福祉センター")
    assert welfare["isEmergencySite"] is False
    assert welfare["isEvacuationCenter"] is True
    assert welfare["isWelfareShelter"] is True
    # 生活する場所には災害種別の指定がない
    assert welfare["disasterTypes"] == []


def test_output_schema(raw_dir, tmp_path):
    out = tmp_path / "nested" / "evacuation_sites.json"
    clean_shelters.clean_shelters(str(raw_dir), str(out))

    assert out.exists()
    with open(out, encoding="utf-8") as fp:
        saved = json.load(fp)

    record = saved[0]
    assert set(record) == {
        "id", "name", "address", "location",
        "isEmergencySite", "isEvacuationCenter", "isWelfareShelter",
        "disasterTypes", "targetOccupants",
    }
    assert isinstance(record["location"]["lat"], float)
    assert isinstance(record["location"]["lng"], float)
    assert record["id"].startswith("evac_")


def test_coordinates_are_parsed_as_numbers(raw_dir, tmp_path):
    out = tmp_path / "out.json"
    records = clean_shelters.clean_shelters(str(raw_dir), str(out))
    for r in records:
        assert 20.0 <= r["location"]["lat"] <= 46.0
        assert 122.0 <= r["location"]["lng"] <= 154.0


def test_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        clean_shelters.clean_shelters(str(tmp_path / "nope"), str(tmp_path / "o.json"))


def test_rows_without_coordinates_are_skipped(tmp_path):
    """緯度経度が欠けた行は地図に描けないため除外する。"""
    raw = tmp_path / "raw"
    d = raw / "designated_evacuation_center"
    d.mkdir(parents=True)
    broken = (
        "NO,共通ID,施設・場所名,住所,洪水,崖崩れ、土石流及び地滑り,高潮,地震,津波,"
        "大規模な火事,内水氾濫,火山現象,指定避難所との住所同一,緯度,経度,備考\n"
        "1,E1,良い施設,住所1,1,,,,,,,,,35.52,139.72,\n"
        "2,E2,壊れた施設,住所2,1,,,,,,,,,,,\n"
    )
    d.joinpath("kanagawa_pref.csv").write_text(broken, encoding="utf-8-sig")

    records = clean_shelters.clean_shelters(str(raw), str(tmp_path / "o.json"))
    assert [r["name"] for r in records] == ["良い施設"]
