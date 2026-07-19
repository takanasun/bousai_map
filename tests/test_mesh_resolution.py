"""人口メッシュの解像度切り替えのテスト。

解像度は元データ（KEY_CODE の桁数）で決まるため、1km のデータから 500m を
生成することはできない。したがって「選べる解像度」は **配置されている
データファイル** で決まる。この前提が壊れると UI に存在しない選択肢が
出てしまうため、可用性の判定をテストで固定する。

テストは conftest により `tests/fixtures/` を参照する。
そこには 1km と 500m のフィクスチャを置いてある（250m は意図的に無し）。
"""

from __future__ import annotations

import function_app
from src.datastore import get_datastore
from src.services import infra


# --- 可用性の判定 ----------------------------------------------------------- #

def test_available_resolutions_reflect_existing_files():
    """存在するファイルだけが選択肢になる（250m は未配置なので出ない）。"""
    available = infra.available_mesh_resolutions()
    assert "1km" in available
    assert "500m" in available
    assert "250m" not in available


def test_available_resolutions_are_ordered_coarse_to_fine():
    assert infra.available_mesh_resolutions() == ["1km", "500m"]


def test_default_resolution_is_the_coarsest_available():
    """既定は最も粗い＝最も軽い解像度。初期表示を重くしないため。"""
    assert infra.default_mesh_resolution() == "1km"


def test_datastore_exists_does_not_raise_for_missing():
    store = get_datastore()
    assert store.exists("population_mesh_1km") is True
    assert store.exists("population_mesh_250m") is False


# --- 読み込み --------------------------------------------------------------- #

def test_load_mesh_defaults_to_coarsest(load_fixture):
    expected = load_fixture("population_mesh_1km")
    assert len(infra.load_mesh()) == len(expected)


def test_load_mesh_with_explicit_resolution(load_fixture):
    expected = load_fixture("population_mesh_500m")
    items = infra.load_mesh("500m")
    assert len(items) == len(expected)
    # 500m のコードは9桁
    assert all(len(i["meshId"]) == 9 for i in items)


def test_load_mesh_unknown_resolution_raises():
    import pytest

    with pytest.raises(KeyError):
        infra.load_mesh("2km")


def test_load_mesh_unavailable_resolution_raises():
    import pytest

    with pytest.raises(KeyError):
        infra.load_mesh("250m")


# --- ルート ----------------------------------------------------------------- #

def test_mesh_route_returns_available_resolutions(invoke):
    status, body = invoke(function_app.mesh)
    assert status == 200
    assert body["availableResolutions"] == ["1km", "500m"]
    assert body["resolution"] == "1km"


def test_mesh_route_with_resolution_param(invoke, load_fixture):
    expected = load_fixture("population_mesh_500m")
    status, body = invoke(function_app.mesh, params={"resolution": "500m"})
    assert status == 200
    assert body["resolution"] == "500m"
    assert body["count"] == len(expected)


def test_mesh_route_rejects_unavailable_resolution(invoke):
    """未配置の解像度は 404 + 利用可能な一覧を案内する。"""
    status, body = invoke(function_app.mesh, params={"resolution": "250m"})
    assert status == 404
    assert "250m" in body["error"]
    assert "1km" in body["error"]


def test_mesh_route_rejects_unknown_resolution(invoke):
    status, body = invoke(function_app.mesh, params={"resolution": "2km"})
    assert status == 404
    assert "error" in body


def test_mesh_route_empty_resolution_falls_back_to_default(invoke):
    status, body = invoke(function_app.mesh, params={"resolution": ""})
    assert status == 200
    assert body["resolution"] == "1km"
