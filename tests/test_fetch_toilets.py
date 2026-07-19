"""`scripts/fetch_toilets.py` の単体テスト。

ネットワークには一切アクセスせず、Overpass API の応答を模したモック JSON を
用いてクエリ生成・パース・クレンジング・保存の各処理を検証する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ を import パスに追加（pytest.ini の pythonpath はプロジェクトルートのみ）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import fetch_toilets  # noqa: E402


# ---------------------------------------------------------------------------
# モックデータ
# ---------------------------------------------------------------------------

def _overpass_payload(elements):
    """Overpass API の応答形式に包む。"""
    return {"version": 0.6, "generator": "Overpass API (mock)", "elements": elements}


NODE_FULL = {
    "type": "node",
    "id": 1001,
    "lat": 35.4478,
    "lon": 139.6425,
    "tags": {
        "amenity": "toilets",
        "name": "横浜駅東口公衆トイレ",
        "wheelchair": "yes",
        "toilets:ostomate": "yes",
        "opening_hours": "24/7",
    },
}

NODE_MINIMAL = {
    "type": "node",
    "id": 1002,
    "lat": 35.3106,
    "lon": 139.5500,
    "tags": {"amenity": "toilets"},
}

WAY_WITH_CENTER = {
    "type": "way",
    "id": 2001,
    "center": {"lat": 35.2000, "lon": 139.1000},
    "tags": {
        "amenity": "toilets",
        "name": "小田原城址公園便所",
        "toilets:wheelchair": "yes",
    },
}

RELATION_WITH_CENTER = {
    "type": "relation",
    "id": 3001,
    "center": {"lat": 35.6000, "lon": 139.4000},
    "tags": {"amenity": "toilets", "name:ja": "多摩川河川敷トイレ"},
}

NODE_NO_COORDS = {
    "type": "way",
    "id": 2002,
    "tags": {"amenity": "toilets", "name": "座標なしトイレ"},
}


# ---------------------------------------------------------------------------
# build_query
# ---------------------------------------------------------------------------

def test_build_query_uses_area_name_by_default():
    query = fetch_toilets.build_query()
    assert "神奈川県" in query
    assert 'admin_level"="4' in query
    # node / way / relation をすべて取得すること
    assert "node[" in query and "way[" in query and "relation[" in query
    # way/relation の代表点を得るため out center が必要
    assert "out center" in query
    assert "[out:json]" in query


def test_build_query_matches_both_toilet_and_toilets_tag():
    """OSM の正式タグは amenity=toilets。誤記の amenity=toilet も拾う。"""
    query = fetch_toilets.build_query()
    assert "toilets?" in query  # 正規表現 ^toilets?$


def test_build_query_with_bbox_does_not_use_area():
    bbox = (35.10, 138.90, 35.70, 139.80)
    query = fetch_toilets.build_query(bbox=bbox)
    assert "area" not in query
    assert "35.1,138.9,35.7,139.8" in query


def test_build_query_respects_timeout():
    query = fetch_toilets.build_query(timeout=300)
    assert "[timeout:300]" in query


# ---------------------------------------------------------------------------
# _is_yes / タグ判定ヘルパ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["yes", "YES", " yes ", "designated"])
def test_is_yes_truthy(value):
    assert fetch_toilets._is_yes(value) is True


@pytest.mark.parametrize("value", ["no", "limited", "", None, "unknown", "1"])
def test_is_yes_falsy(value):
    assert fetch_toilets._is_yes(value) is False


@pytest.mark.parametrize(
    "tags",
    [
        {"wheelchair": "yes"},
        {"toilets:wheelchair": "yes"},
        {"wheelchair": "designated"},
    ],
)
def test_detect_accessible_true(tags):
    assert fetch_toilets.detect_accessible(tags) is True


@pytest.mark.parametrize(
    "tags",
    [{}, {"wheelchair": "no"}, {"wheelchair": "limited"}, {"toilets:wheelchair": "no"}],
)
def test_detect_accessible_false(tags):
    """タグが無い/限定的な場合は False（安全側に倒す）。"""
    assert fetch_toilets.detect_accessible(tags) is False


@pytest.mark.parametrize(
    "tags",
    [
        {"ostomate": "yes"},
        {"toilets:ostomate": "yes"},
        {"amenity:ostomate": "yes"},
        {"ostomate_facility": "yes"},
    ],
)
def test_detect_ostomate_true(tags):
    assert fetch_toilets.detect_ostomate(tags) is True


@pytest.mark.parametrize("tags", [{}, {"ostomate": "no"}, {"wheelchair": "yes"}])
def test_detect_ostomate_false(tags):
    assert fetch_toilets.detect_ostomate(tags) is False


@pytest.mark.parametrize(
    "tags",
    [
        {"opening_hours": "24/7"},
        {"opening_hours": " 24/7 "},
        {"opening_hours": "Mo-Su 00:00-24:00"},
        {"opening_hours": "00:00-24:00"},
    ],
)
def test_detect_open24h_true(tags):
    assert fetch_toilets.detect_open24h(tags) is True


@pytest.mark.parametrize(
    "tags",
    [{}, {"opening_hours": "09:00-17:00"}, {"opening_hours": "Mo-Fr 08:00-20:00"}],
)
def test_detect_open24h_false(tags):
    assert fetch_toilets.detect_open24h(tags) is False


# ---------------------------------------------------------------------------
# transform_element
# ---------------------------------------------------------------------------

def test_transform_node_full_matches_spec_schema():
    record = fetch_toilets.transform_element(NODE_FULL)
    assert record == {
        "id": "toilet_n1001",
        "name": "横浜駅東口公衆トイレ",
        "location": {"lat": 35.4478, "lng": 139.6425},
        "attributes": {"accessible": True, "ostomate": True, "open24h": True},
    }


def test_transform_uses_default_name_when_missing():
    record = fetch_toilets.transform_element(NODE_MINIMAL)
    assert record["name"] == fetch_toilets.DEFAULT_NAME
    assert record["attributes"] == {
        "accessible": False,
        "ostomate": False,
        "open24h": False,
    }


def test_transform_way_uses_center_coordinates():
    record = fetch_toilets.transform_element(WAY_WITH_CENTER)
    assert record["id"] == "toilet_w2001"
    assert record["location"] == {"lat": 35.2000, "lng": 139.1000}
    assert record["attributes"]["accessible"] is True


def test_transform_relation_falls_back_to_name_ja():
    record = fetch_toilets.transform_element(RELATION_WITH_CENTER)
    assert record["id"] == "toilet_r3001"
    assert record["name"] == "多摩川河川敷トイレ"


def test_transform_returns_none_without_coordinates():
    assert fetch_toilets.transform_element(NODE_NO_COORDS) is None


def test_transform_returns_none_for_out_of_range_coordinates():
    broken = {"type": "node", "id": 9, "lat": 999.0, "lon": 139.0, "tags": {}}
    assert fetch_toilets.transform_element(broken) is None


def test_transform_returns_none_for_non_numeric_coordinates():
    broken = {"type": "node", "id": 9, "lat": "N/A", "lon": 139.0, "tags": {}}
    assert fetch_toilets.transform_element(broken) is None


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------

def test_parse_response_transforms_all_valid_elements():
    payload = _overpass_payload(
        [NODE_FULL, NODE_MINIMAL, WAY_WITH_CENTER, RELATION_WITH_CENTER, NODE_NO_COORDS]
    )
    records = fetch_toilets.parse_response(payload)
    # 座標なしの1件は除外される
    assert len(records) == 4
    assert {r["id"] for r in records} == {
        "toilet_n1001",
        "toilet_n1002",
        "toilet_w2001",
        "toilet_r3001",
    }


def test_parse_response_deduplicates_by_id():
    payload = _overpass_payload([NODE_FULL, dict(NODE_FULL)])
    records = fetch_toilets.parse_response(payload)
    assert len(records) == 1


def test_parse_response_is_sorted_by_id():
    payload = _overpass_payload([RELATION_WITH_CENTER, NODE_FULL, WAY_WITH_CENTER])
    records = fetch_toilets.parse_response(payload)
    ids = [r["id"] for r in records]
    assert ids == sorted(ids)


def test_parse_response_handles_empty_elements():
    assert fetch_toilets.parse_response(_overpass_payload([])) == []


def test_parse_response_raises_on_malformed_payload():
    with pytest.raises(fetch_toilets.FetchError):
        fetch_toilets.parse_response({"no_elements_key": True})


# ---------------------------------------------------------------------------
# fetch_overpass（requests をモック）
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text="{}"):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text

    def json(self):
        if not isinstance(self._payload, dict):
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise fetch_toilets.requests.HTTPError(f"status {self.status_code}")


class _FakeSession:
    """`post` の戻り値を順に返すスタブ。例外インスタンスなら raise する。"""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def post(self, url, data=None, timeout=None, headers=None):
        self.calls.append({"url": url, "data": data, "timeout": timeout})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_fetch_overpass_returns_payload_on_success():
    payload = _overpass_payload([NODE_FULL])
    session = _FakeSession([_FakeResponse(payload)])
    result = fetch_toilets.fetch_overpass("[out:json];", session=session, retry_wait=0)
    assert result == payload
    assert session.calls[0]["data"] == {"data": "[out:json];"}


def test_fetch_overpass_falls_back_to_next_endpoint():
    payload = _overpass_payload([NODE_MINIMAL])
    session = _FakeSession(
        [
            fetch_toilets.requests.ConnectionError("boom"),
            _FakeResponse(payload),
        ]
    )
    result = fetch_toilets.fetch_overpass(
        "[out:json];",
        endpoints=["https://a.example/api", "https://b.example/api"],
        session=session,
        retries=1,
        retry_wait=0,
    )
    assert result == payload
    assert len(session.calls) == 2
    assert session.calls[0]["url"] == "https://a.example/api"
    assert session.calls[1]["url"] == "https://b.example/api"


def test_fetch_overpass_retries_same_endpoint_before_failing_over():
    payload = _overpass_payload([])
    session = _FakeSession(
        [
            fetch_toilets.requests.Timeout("slow"),
            _FakeResponse(payload),
        ]
    )
    result = fetch_toilets.fetch_overpass(
        "[out:json];",
        endpoints=["https://a.example/api"],
        session=session,
        retries=2,
        retry_wait=0,
    )
    assert result == payload
    assert len(session.calls) == 2


def test_fetch_overpass_raises_fetch_error_when_all_endpoints_fail():
    session = _FakeSession(
        [
            fetch_toilets.requests.ConnectionError("boom"),
            fetch_toilets.requests.ConnectionError("boom"),
        ]
    )
    with pytest.raises(fetch_toilets.FetchError):
        fetch_toilets.fetch_overpass(
            "[out:json];",
            endpoints=["https://a.example/api", "https://b.example/api"],
            session=session,
            retries=1,
            retry_wait=0,
        )


def test_fetch_overpass_raises_fetch_error_on_invalid_json():
    session = _FakeSession([_FakeResponse(payload="not-a-dict", text="<html>429</html>")])
    with pytest.raises(fetch_toilets.FetchError):
        fetch_toilets.fetch_overpass(
            "[out:json];",
            endpoints=["https://a.example/api"],
            session=session,
            retries=1,
            retry_wait=0,
        )


# ---------------------------------------------------------------------------
# save_records
# ---------------------------------------------------------------------------

def test_save_records_writes_utf8_json_and_creates_parent_dir(tmp_path):
    out = tmp_path / "nested" / "toilets.json"
    records = [fetch_toilets.transform_element(NODE_FULL)]
    fetch_toilets.save_records(records, str(out))

    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded == records
    # 日本語が \uXXXX にエスケープされていないこと
    assert "横浜駅東口公衆トイレ" in out.read_text(encoding="utf-8")


def test_save_records_refuses_empty_result_by_default(tmp_path):
    out = tmp_path / "toilets.json"
    with pytest.raises(fetch_toilets.FetchError):
        fetch_toilets.save_records([], str(out))
    assert not out.exists()


def test_save_records_allows_empty_when_forced(tmp_path):
    out = tmp_path / "toilets.json"
    fetch_toilets.save_records([], str(out), allow_empty=True)
    assert json.loads(out.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------
# main（ネットワーク層のみ差し替え）
# ---------------------------------------------------------------------------

def test_main_writes_output_file(tmp_path, monkeypatch):
    out = tmp_path / "toilets.json"
    monkeypatch.setattr(
        fetch_toilets,
        "fetch_overpass",
        lambda *a, **kw: _overpass_payload([NODE_FULL, WAY_WITH_CENTER]),
    )
    exit_code = fetch_toilets.main(["--output", str(out)])
    assert exit_code == 0
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert len(loaded) == 2


def test_main_returns_nonzero_on_fetch_error(tmp_path, monkeypatch):
    out = tmp_path / "toilets.json"

    def _boom(*a, **kw):
        raise fetch_toilets.FetchError("network down")

    monkeypatch.setattr(fetch_toilets, "fetch_overpass", _boom)
    assert fetch_toilets.main(["--output", str(out)]) == 1
    assert not out.exists()
