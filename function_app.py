"""Azure Functions アプリケーション エントリポイント (Python V2 Programming Model)。

`func start` で起動され、各 HTTP ルートを公開する。
本ファイルは「薄いハンドラ層」に徹し、実際のロジックは `src/services` に委譲する。

ローカル開発では DATA_STORE_BACKEND=local / GEOCODER_BACKEND=mock により
Azure クラウドへ一切接続せずに完結する(local.settings.json / .env 参照)。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import azure.functions as func

from src import config
from src.datastore import DataStoreError
from src.services import geocoding, infra, llm, ratelimit

logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# AIへの質問の最大文字数。プロンプト肥大とコスト増を防ぐ。
MAX_QUESTION_LENGTH = 500


# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #
def _json_response(
    payload: Any,
    status_code: int = 200,
    headers: Optional[dict] = None,
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
        headers=headers,
    )


def _error_response(
    message: str,
    status_code: int,
    headers: Optional[dict] = None,
) -> func.HttpResponse:
    """仕様 7.1 に従い、画面を真っ白にしないための JSON エラーレスポンス。"""
    return _json_response({"error": message}, status_code=status_code, headers=headers)


def _candidate(item: Any, kind: str) -> dict:
    """AI に渡した施設を、フロントが地図上で強調するための最小情報に絞る。"""
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "kind": kind,  # 'shelter' | 'hospital' | 'toilet'
        "location": item.get("location"),
        "distanceKm": item.get("distanceKm"),
    }


def _parse_float(req: func.HttpRequest, name: str) -> Optional[float]:
    raw = req.params.get(name)
    if raw is None or raw == "":
        return None
    return float(raw)  # ValueError は呼び出し側で捕捉


# --------------------------------------------------------------------------- #
# ルート
# --------------------------------------------------------------------------- #
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """死活監視用エンドポイント。"""
    return _json_response({"status": "ok", "service": "bousai-map-api"})


@app.route(route="config", methods=["GET"])
def map_config(req: func.HttpRequest) -> func.HttpResponse:
    """フロントエンドに必要な設定を配布する。

    Azure Maps のキーを JS にハードコードせず、環境変数から読んでここで返す。

    注意: 共有キー認証である以上、キーはブラウザから参照可能になる。
    本番では Azure Maps アカウント側でキーをローテーション可能にしておくか、
    Microsoft Entra ID 認証 + トークン発行方式へ移行すること。
    """
    key = config.azure_maps_subscription_key()

    # AI アシスタントが使えるかと、未設定時の案内文をフロントへ配る。
    # 案内文をフロントに書くと、バックエンドを切り替えたときに嘘の項目名を
    # 表示してしまうため、選択中の実装自身に語らせる。
    # LLM_BACKEND の値が不正でも地図は使えるべきなので、ここでは落とさない。
    try:
        llm_client = llm.get_llm_client()
        chat_configured = llm_client.is_configured()
        chat_hint = "" if chat_configured else llm_client.configuration_hint()
    except llm.LLMError as exc:
        logger.warning("LLM の設定が不正です: %s", exc)
        chat_configured = False
        chat_hint = "LLM_BACKEND の設定値が不正です。.env を確認してください。"

    payload = {
        "azureMapsKey": key,
        "configured": bool(key),
        "chatConfigured": chat_configured,
        "chatConfigHint": chat_hint,
    }
    if not key:
        payload["message"] = (
            "Azure Maps のキーが未設定です。"
            ".env の AZURE_MAPS_SUBSCRIPTION_KEY を設定してください。"
        )
    return _json_response(payload)


@app.route(route="geocode", methods=["GET"])
def geocode(req: func.HttpRequest) -> func.HttpResponse:
    """住所→緯度経度変換 (仕様 5.1)。 例: /api/geocode?address=川崎市"""
    address = req.params.get("address", "")
    if not address.strip():
        return _error_response("address パラメータが必要です", 400)

    try:
        geocoder = geocoding.get_geocoder()
        point = geocoder.geocode(address)
    except geocoding.GeocodingError as exc:
        # 外部API障害・設定不備。利用者向けの文言をそのまま返す
        logger.warning("住所検索に失敗しました: %s", exc)
        return _error_response(str(exc), 502)
    except DataStoreError:
        logger.exception("ジオコーディング用データの読み込みに失敗しました")
        return _error_response("データが取得できませんでした", 502)
    except Exception:  # noqa: BLE001 - 最後の砦。画面を真っ白にしない
        logger.exception("ジオコーディング処理で予期せぬエラーが発生しました")
        return _error_response("サーバー内部エラーが発生しました", 500)

    if point is None:
        return _error_response("住所に該当する場所が見つかりませんでした", 404)

    # 対応エリア外は 422。県外を基準にすると周辺施設が0件になり、
    # 利用者は「該当なし」なのか「対象外」なのか区別できない。
    if not geocoding.is_supported_area(point.get("title")):
        found = point.get("prefecture") or "対象外の地域"
        return _error_response(
            f"このアプリは{geocoding.SUPPORTED_PREFECTURE}のみに対応しています"
            f"（入力された住所は{found}でした）。"
            f"{geocoding.SUPPORTED_PREFECTURE}内の住所を入力してください。",
            422,
        )

    return _json_response(
        {
            "address": address,
            "location": {"lat": point["lat"], "lng": point["lng"]},
            "matchedAddress": point.get("title", ""),
        }
    )


@app.route(route="hospitals", methods=["GET"])
def hospitals(req: func.HttpRequest) -> func.HttpResponse:
    """医療機関一覧 (仕様 5.2 / 5.3)。

    任意クエリ: lat, lng, radius(km), capability(診療科)
    """
    try:
        lat = _parse_float(req, "lat")
        lng = _parse_float(req, "lng")
        radius = _parse_float(req, "radius")
    except ValueError:
        return _error_response("lat/lng/radius は数値で指定してください", 400)

    try:
        items = infra.load_dataset("hospitals")
    except DataStoreError:
        logger.exception("医療機関データの読み込みに失敗しました")
        return _error_response("データが取得できませんでした", 502)

    capability = req.params.get("capability", "").strip()
    if capability:
        items = infra.filter_hospitals_by_capability(items, capability)

    if lat is not None and lng is not None and radius is not None:
        items = infra.filter_by_radius(items, lat, lng, radius)

    return _json_response({"count": len(items), "items": items})


@app.route(route="evacuation", methods=["GET"])
def evacuation(req: func.HttpRequest) -> func.HttpResponse:
    """避難所一覧。任意クエリ: lat, lng, radius(km), welfare(true で福祉避難所のみ)"""
    try:
        lat = _parse_float(req, "lat")
        lng = _parse_float(req, "lng")
        radius = _parse_float(req, "radius")
    except ValueError:
        return _error_response("lat/lng/radius は数値で指定してください", 400)

    try:
        items = infra.load_dataset("evacuation")
    except DataStoreError:
        logger.exception("避難所データの読み込みに失敗しました")
        return _error_response("データが取得できませんでした", 502)

    if req.params.get("welfare", "").lower() == "true":
        items = [e for e in items if e.get("isWelfareShelter") or e.get("isWelfare")]

    if lat is not None and lng is not None and radius is not None:
        items = infra.filter_by_radius(items, lat, lng, radius)

    return _json_response({"count": len(items), "items": items})


@app.route(route="toilets", methods=["GET"])
def toilets(req: func.HttpRequest) -> func.HttpResponse:
    """公衆トイレ一覧。任意クエリ: lat, lng, radius(km), multifunction(true で多機能のみ)"""
    try:
        lat = _parse_float(req, "lat")
        lng = _parse_float(req, "lng")
        radius = _parse_float(req, "radius")
    except ValueError:
        return _error_response("lat/lng/radius は数値で指定してください", 400)

    try:
        items = infra.load_dataset("toilets")
    except DataStoreError:
        logger.exception("トイレデータの読み込みに失敗しました")
        return _error_response("データが取得できませんでした", 502)

    if req.params.get("multifunction", "").lower() == "true":
        items = infra.filter_multifunction_toilets(items)

    if lat is not None and lng is not None and radius is not None:
        items = infra.filter_by_radius(items, lat, lng, radius)

    return _json_response({"count": len(items), "items": items})


@app.route(route="landprice", methods=["GET"])
def landprice(req: func.HttpRequest) -> func.HttpResponse:
    """地価公示の地点一覧（円/㎡）。

    任意クエリ: maxPrice(円/㎡), lat, lng, radius(km)
    `areas` には町名ごとの地価と徒歩圏の防災施設数を集計して返す。
    """
    try:
        max_price = _parse_float(req, "maxPrice")
        lat = _parse_float(req, "lat")
        lng = _parse_float(req, "lng")
        radius = _parse_float(req, "radius")
    except ValueError:
        return _error_response("maxPrice/lat/lng/radius は数値で指定してください", 400)

    try:
        items = infra.load_dataset("landprice")
        # 集計はビルド時に済ませてある（毎回計算すると50秒超かかる）
        areas = infra.load_landprice_areas()
    except DataStoreError:
        logger.exception("地価データの読み込みに失敗しました")
        return _error_response("データが取得できませんでした", 502)

    # スライダーの範囲は絞り込み前の全体から決める（絞るたびに範囲が動かないように）
    all_prices = [i["pricePerSqm"] for i in items if i.get("pricePerSqm")]

    items = infra.filter_by_max_price(items, max_price)
    areas = [a for a in areas if max_price is None or a.get("avgPricePerSqm", 0) <= max_price]

    if lat is not None and lng is not None and radius is not None:
        items = infra.filter_by_radius(items, lat, lng, radius)

    prices = all_prices

    return _json_response(
        {
            "count": len(items),
            "items": items,
            "areas": areas,
            # スライダーの範囲をフロントが決められるよう実データの幅を返す
            "priceRange": {
                "min": min(prices) if prices else 0,
                "max": max(prices) if prices else 0,
            },
        }
    )


@app.route(route="mesh", methods=["GET"])
def mesh(req: func.HttpRequest) -> func.HttpResponse:
    """人口密度メッシュ一覧 (仕様 5.2)。

    任意クエリ: resolution ("1km" / "500m" / "250m" / "125m")。
    省略時は利用可能なうち最も粗い解像度を返す。

    解像度は元データ(KEY_CODE の桁数)で決まるため、対応する CSV を取り込んで
    `scripts/clean_mesh.py` を実行しない限り選択肢は増えない。
    `availableResolutions` に実際に選べる一覧を含めて返す。
    """
    requested = req.params.get("resolution", "").strip() or None

    try:
        available = infra.available_mesh_resolutions()
    except DataStoreError:
        logger.exception("メッシュ解像度の一覧取得に失敗しました")
        return _error_response("データが取得できませんでした", 502)

    if not available:
        return _error_response("人口メッシュのデータが配置されていません", 404)

    if requested is not None and requested not in available:
        return _error_response(
            f"指定された解像度は利用できません: {requested}"
            f"（利用可能: {', '.join(available)}）",
            404,
        )

    try:
        items = infra.load_mesh(requested)
    except KeyError:
        logger.exception("メッシュデータの読み込みに失敗しました")
        return _error_response("人口メッシュのデータが取得できませんでした", 404)
    except DataStoreError:
        logger.exception("メッシュデータの読み込みに失敗しました")
        return _error_response("データが取得できませんでした", 502)

    resolution = requested or infra.default_mesh_resolution()
    return _json_response(
        {
            "count": len(items),
            "resolution": resolution,
            "availableResolutions": available,
            "items": items,
        }
    )


@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """生成AI防災アシスタント。

    リクエスト(JSON): {"question": "...", "lat": 35.5, "lng": 139.7}

    基準地点の周辺データ（避難所・医療機関・トイレ）をサーバ側で集め、
    プロンプトに埋め込んで LLM へ中継する。LLM には検索結果しか渡さないため、
    データベース全体を外部へ送信することはない。

    資格情報は `.env` 経由で環境変数から読み込む（ハードコード禁止）。
    """
    # --- 入力の検証 ---
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("リクエストの JSON を解釈できませんでした", 400)

    if not isinstance(body, dict):
        return _error_response("リクエストは JSON オブジェクトで送ってください", 400)

    question = str(body.get("question") or "").strip()
    if not question:
        return _error_response("question は必須です", 400)
    if len(question) > MAX_QUESTION_LENGTH:
        return _error_response(
            f"質問は{MAX_QUESTION_LENGTH}文字以内で入力してください", 400
        )

    try:
        lat = float(body["lat"])
        lng = float(body["lng"])
    except (KeyError, TypeError, ValueError):
        return _error_response("lat / lng を数値で指定してください", 400)

    # --- レート制限 ---
    # 認証なしで公開するため、ここが従量課金に対する一次防衛線になる。
    # 入力検証より後に置くのは、400 で弾かれる不正リクエストに
    # 正規利用者の枠を消費させないため。
    verdict = ratelimit.get_rate_limiter().check(ratelimit.client_ip(req.headers))
    if not verdict.allowed:
        minutes = max(1, verdict.retry_after_seconds // 60)
        if verdict.scope == "global":
            message = (
                "本日の利用上限に達しました。"
                "個人運営のデモのため回数を制限しています。明日また試してください。"
            )
        else:
            message = f"質問が多すぎます。約{minutes}分後にもう一度お試しください。"
        logger.info("レート制限により拒否しました (scope=%s)", verdict.scope)
        return _error_response(
            message, 429, headers={"Retry-After": str(verdict.retry_after_seconds)}
        )

    # --- LLM が使えるか先に確認（設定漏れと障害を区別する） ---
    try:
        client = llm.get_llm_client()
    except llm.LLMError as exc:
        logger.error("LLM クライアントの生成に失敗しました: %s", exc)
        return _error_response("AIサービスの設定が不正です", 503)

    if not client.is_configured():
        # 設定すべき項目はバックエンドごとに違うため、クライアント自身に案内させる
        return _error_response(
            f"AIアシスタントが未設定です。{client.configuration_hint()}",
            503,
        )

    # --- 周辺データを集める ---
    radius = config.chat_search_radius_km()
    try:
        shelters = infra.filter_by_radius(infra.load_dataset("evacuation"), lat, lng, radius)
        nearby_hospitals = infra.filter_by_radius(
            infra.load_dataset("hospitals"), lat, lng, radius
        )
        nearby_toilets = infra.filter_by_radius(
            infra.load_dataset("toilets"), lat, lng, radius
        )
        # 地価は「安いエリア」の比較に使うため、基準地点の周辺に限らず
        # 対象市区全体を渡す（近所だけだと比較にならない）
        land_areas = infra.load_landprice_areas()
    except DataStoreError:
        logger.exception("周辺データの読み込みに失敗しました")
        return _error_response("周辺データが取得できませんでした", 502)

    # トークン量を抑えるため近い順に上限を設ける
    shelters = shelters[:15]
    nearby_hospitals = nearby_hospitals[:15]
    nearby_toilets = nearby_toilets[:10]

    # 地価と施設数はビルド時に集計済み。AIに計算させると誤るため数値は渡すだけ。
    # 全1,178町を渡すとトークンが膨らむので、安い順に絞る。
    areas = land_areas[:40]

    context = llm.build_context(
        {"lat": lat, "lng": lng}, shelters, nearby_hospitals, nearby_toilets, areas
    )

    # --- LLM へ中継 ---
    try:
        answer = client.complete(
            llm.SYSTEM_PROMPT, llm.build_user_message(question, context)
        )
    except llm.LLMError as exc:
        logger.warning("LLM 呼び出しに失敗しました: %s", exc)
        return _error_response(str(exc), 502)
    except Exception:  # noqa: BLE001 - 最後の砦。画面を真っ白にしない
        logger.exception("AI 応答の生成で予期せぬエラーが発生しました")
        return _error_response("AIの応答生成に失敗しました", 500)

    # AIに渡した施設を id/name 付きで返す。フロントは回答文に名前が現れた
    # ものを地図上で強調する（AIにID列挙させるとハルシネーションの恐れがある）。
    candidates = (
        [_candidate(s, "shelter") for s in shelters]
        + [_candidate(h, "hospital") for h in nearby_hospitals]
        + [_candidate(t, "toilet") for t in nearby_toilets]
    )

    return _json_response(
        {
            "answer": answer,
            "sources": {
                "shelters": len(shelters),
                "hospitals": len(nearby_hospitals),
                "toilets": len(nearby_toilets),
                "areas": len(areas),
                "radiusKm": radius,
            },
            "candidates": candidates,
        }
    )
