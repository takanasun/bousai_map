"""`scripts/clean_landprice.py` の単体テスト。

国土数値情報「地価公示」(L01) から、対象エリアの地点別地価を抽出する。

このデータ固有の事情:
  1. プロパティ名が L01_006 のようなコードで、意味が分からない
  2. 住所に全角スペースが混ざる（"神奈川県　川崎市…"）
  3. 用途は "住宅,店舗" のようにカンマ区切りの複合値
  4. 神奈川県全域1,787地点のうち川崎区は41地点
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import clean_landprice  # noqa: E402


def _feature(price, city_code, address, use="住宅", lng=139.72, lat=35.52, year="2021"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": {
            clean_landprice.PRICE_KEY: price,
            clean_landprice.CITY_CODE_KEY: city_code,
            clean_landprice.ADDRESS_KEY: address,
            clean_landprice.USE_KEY: use,
            clean_landprice.YEAR_KEY: year,
        },
    }


@pytest.fixture
def geojson_path(tmp_path):
    data = {
        "type": "FeatureCollection",
        "features": [
            _feature("272000", "14131", "神奈川県　川崎市川崎区中瀬２−１７−１２"),
            _feature("321000", "14131", "神奈川県　川崎市川崎区旭町１−１１−１２", use="住宅,店舗"),
            _feature("92000", "14131", "神奈川県　川崎市川崎区殿町３−１", use="工場"),
            _feature("500000", "14101", "神奈川県　横浜市鶴見区下末吉２−１５−２"),  # 対象外の市区
            _feature("", "14131", "神奈川県　川崎市川崎区価格なし１"),               # 価格欠損
            _feature("100000", "14131", "神奈川県　川崎市川崎区座標なし１", lng=None, lat=None),
        ],
    }
    path = tmp_path / "L01.geojson"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# --- 市区町村での絞り込み ---------------------------------------------------- #

def test_filters_by_city_code(geojson_path, tmp_path):
    records = clean_landprice.clean_landprice(
        str(geojson_path), str(tmp_path / "o.json"), city_codes=["14131"]
    )
    assert all("川崎区" in r["address"] for r in records)
    assert not any("鶴見区" in r["address"] for r in records)


def test_city_code_is_configurable(geojson_path, tmp_path):
    records = clean_landprice.clean_landprice(
        str(geojson_path), str(tmp_path / "o.json"), city_codes=["14101"]
    )
    assert len(records) == 1
    assert "鶴見区" in records[0]["address"]


def test_prefecture_wide_is_the_default_target():
    """既定は県全域。市区で絞るのは --city-codes を明示したときだけ。"""
    assert clean_landprice.DEFAULT_CITY_CODES is None


# --- 値の正規化 -------------------------------------------------------------- #

def test_price_is_int_yen_per_sqm(geojson_path, tmp_path):
    records = clean_landprice.clean_landprice(
        str(geojson_path), str(tmp_path / "o.json"), city_codes=["14131"]
    )
    for r in records:
        assert isinstance(r["pricePerSqm"], int)
        assert r["pricePerSqm"] > 0


def test_address_full_width_space_is_normalized(geojson_path, tmp_path):
    """「神奈川県　川崎市」の全角スペースを除去する（表示と突合のため）。"""
    records = clean_landprice.clean_landprice(str(geojson_path), str(tmp_path / "o.json"))
    assert all("　" not in r["address"] for r in records)
    assert any(r["address"].startswith("神奈川県川崎市川崎区") for r in records)


def test_uses_are_split_into_list(geojson_path, tmp_path):
    """用途はカンマ区切りの複合値なので配列に分解する。"""
    records = clean_landprice.clean_landprice(str(geojson_path), str(tmp_path / "o.json"))
    by_price = {r["pricePerSqm"]: r for r in records}
    assert by_price[321000]["uses"] == ["住宅", "店舗"]
    assert by_price[272000]["uses"] == ["住宅"]


def test_town_is_extracted_from_address(geojson_path, tmp_path):
    """町名（丁目・番地より前）を取り出す。エリア比較の単位になる。"""
    records = clean_landprice.clean_landprice(str(geojson_path), str(tmp_path / "o.json"))
    towns = {r["town"] for r in records}
    assert "中瀬" in towns
    assert "旭町" in towns


# --- 欠損の除外 -------------------------------------------------------------- #

def test_rows_without_price_are_skipped(geojson_path, tmp_path):
    records = clean_landprice.clean_landprice(
        str(geojson_path), str(tmp_path / "o.json"), city_codes=["14131"]
    )
    assert not any("価格なし" in r["address"] for r in records)


def test_rows_without_coordinates_are_skipped(geojson_path, tmp_path):
    records = clean_landprice.clean_landprice(
        str(geojson_path), str(tmp_path / "o.json"), city_codes=["14131"]
    )
    assert not any("座標なし" in r["address"] for r in records)


# --- 出力 -------------------------------------------------------------------- #

def test_output_schema(geojson_path, tmp_path):
    out = tmp_path / "nested" / "landprice.json"
    clean_landprice.clean_landprice(str(geojson_path), str(out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    record = saved[0]
    assert set(record) == {"id", "address", "town", "location", "pricePerSqm", "uses", "year"}
    assert record["id"].startswith("land_")


def test_sorted_by_price_ascending(geojson_path, tmp_path):
    """安い順に並べる（「安いエリア」の質問で先頭から使えるように）。"""
    records = clean_landprice.clean_landprice(str(geojson_path), str(tmp_path / "o.json"))
    prices = [r["pricePerSqm"] for r in records]
    assert prices == sorted(prices)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        clean_landprice.clean_landprice(str(tmp_path / "nope.geojson"), str(tmp_path / "o.json"))


# ---------------------------------------------------------------------------
# 県全域対応: 区を持たない市町村の町名抽出
#
# 神奈川県1,787地点のうち723件（40%）は区を持たない（横須賀市・愛川町など）。
# 「区」だけを頼りにすると町名が空になり、エリア集計が「（町名不明）」に潰れる。
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("address,expected", [
    # 政令市（区あり）
    ("神奈川県横浜市鶴見区下末吉２−１５−２", "下末吉"),
    ("神奈川県川崎市川崎区中瀬２−１７−１２", "中瀬"),
    ("神奈川県相模原市中央区中央３−１２", "中央"),
    # 区を持たない市
    ("神奈川県横須賀市池田町６−１０−１０", "池田町"),
    ("神奈川県小田原市荻窪３００", "荻窪"),
    ("神奈川県藤沢市鵠沼海岸２−１", "鵠沼海岸"),
    # 郡部（町・村）
    ("神奈川県愛甲郡愛川町角田２１０", "角田"),
    ("神奈川県足柄上郡開成町吉田島１", "吉田島"),
])
def test_extract_town_handles_all_municipality_types(address, expected):
    assert clean_landprice.extract_town(address) == expected


def test_extract_town_prefers_the_last_municipality_marker():
    """「愛甲郡愛川町」のように市区町村マーカーが複数あるとき、最後を使う。"""
    assert clean_landprice.extract_town("神奈川県愛甲郡愛川町角田２１０") == "角田"


def test_extract_town_returns_empty_for_unparseable():
    assert clean_landprice.extract_town("") == ""
    assert clean_landprice.extract_town("住所らしくない文字列") == ""


def test_prefecture_wide_is_the_default():
    """既定は県全域。市区で絞るのは --city-codes を明示したときだけ。"""
    assert clean_landprice.DEFAULT_CITY_CODES is None


def test_no_city_filter_keeps_all(tmp_path):
    data = {
        "type": "FeatureCollection",
        "features": [
            _feature("272000", "14131", "神奈川県　川崎市川崎区中瀬２−１７"),
            _feature("500000", "14101", "神奈川県　横浜市鶴見区下末吉２−１５"),
            _feature("180000", "14201", "神奈川県　横須賀市池田町６−１０"),
        ],
    }
    path = tmp_path / "L01.geojson"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    records = clean_landprice.clean_landprice(str(path), str(tmp_path / "o.json"))
    assert len(records) == 3
    assert {r["town"] for r in records} == {"中瀬", "下末吉", "池田町"}


# ---------------------------------------------------------------------------
# 出力先の分離
#
# 集計の出力先をグローバル定数の既定にしていたため、テストが tmp_path に
# 出力しても集計だけ本番の data/processed に書かれ、実データを壊した。
# ---------------------------------------------------------------------------

def _seed_facilities(directory):
    """集計に必要な加工済みデータを置く（無いと集計はスキップされる）。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "evacuation_sites.json").write_text(
        json.dumps([{"id": "s1", "name": "避難所", "location": {"lat": 35.52, "lng": 139.72}}]),
        encoding="utf-8",
    )
    (directory / "hospitals.json").write_text(
        json.dumps([{"id": "h1", "name": "病院", "location": {"lat": 35.52, "lng": 139.72}}]),
        encoding="utf-8",
    )


def test_areas_are_written_next_to_the_points_output(geojson_path, tmp_path):
    out_dir = tmp_path / "sub"
    _seed_facilities(out_dir)
    clean_landprice.clean_landprice(str(geojson_path), str(out_dir / "landprice.json"))
    assert (out_dir / "landprice_areas.json").exists()


def test_areas_are_skipped_without_facility_data(geojson_path, tmp_path):
    """避難所データが無ければ集計しない（空の集計で上書きしない）。"""
    clean_landprice.clean_landprice(str(geojson_path), str(tmp_path / "landprice.json"))
    assert not (tmp_path / "landprice_areas.json").exists()


def test_does_not_write_outside_the_output_directory(geojson_path, tmp_path, monkeypatch):
    """本番の data/processed に書き込まないこと（実データ破壊の回帰ガード）。"""
    written = []
    real_open = open

    def tracking_open(path, mode="r", *args, **kwargs):
        if "w" in mode:
            written.append(str(path))
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    clean_landprice.clean_landprice(str(geojson_path), str(tmp_path / "landprice.json"))

    for path in written:
        assert str(tmp_path) in path, f"出力先ディレクトリの外に書き込んでいます: {path}"


def test_areas_output_path_is_overridable(geojson_path, tmp_path):
    _seed_facilities(tmp_path)
    areas = tmp_path / "custom_areas.json"
    clean_landprice.clean_landprice(
        str(geojson_path), str(tmp_path / "landprice.json"), areas_output_path=str(areas)
    )
    assert areas.exists()
