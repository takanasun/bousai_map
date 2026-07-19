"""ジオコーディング(住所→緯度経度)サービス。

    GEOCODER_BACKEND=gsi        … 国土地理院 住所検索API（既定・キー不要）
    GEOCODER_BACKEND=azure_maps … Azure Maps Search API
    GEOCODER_BACKEND=mock       … ローカル辞書（外部通信なし。テスト用）

既定を国土地理院にしている理由:
    Azure Maps は日本の住所をほとんど解決できなかった（実測）。
      市街地の丁目・番地レベルの住所  → address:0件 / fuzzy:9〜15km ずれ
      「横浜市中区日本大通1(県庁)」   → address:0件 / fuzzy: 9.8km ずれ
    国土地理院は同じ住所を 1.4km / 0.01km で解決できたため、
    誤った避難所を案内しないよう既定を切り替えた。

資格情報は環境変数からのみ読み込み、ソースにハードコードしない。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TypedDict

from .. import config
from ..datastore import DataStore, get_datastore

logger = logging.getLogger(__name__)

FIXTURE_RESOURCE = "geocode_fixtures"

# 外部API呼び出しのタイムアウト（秒）
REQUEST_TIMEOUT_SECONDS = 10

AZURE_MAPS_SEARCH_URL = "https://atlas.microsoft.com/search/address/json"

# 国土地理院 住所検索API（キー不要の公開API）
GSI_SEARCH_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"

# 公共APIなので素性を明示する
USER_AGENT = "bousai-map/0.1 (disaster preparedness map)"

# 日本国内の緯度経度の範囲。明らかに国外の座標は誤りとして弾く。
JAPAN_LAT_RANGE = (20.0, 46.0)
JAPAN_LNG_RANGE = (122.0, 154.0)


# 対応エリア。避難所・医療機関・トイレのデータが神奈川県分しか無いため、
# 県外を基準にすると「周辺0件」の地図とAI回答になってしまう。
SUPPORTED_PREFECTURE = "神奈川県"

# 都道府県は47件しかないので前方一致で判定する。
#
# 接尾辞（都/道/府/県）を先頭から探す方式は「京都府」を「京都」と誤判定する
# （2文字目が「都」のため）。同様に「東京都」も「東京」で切れてしまうため、
# 実在する名称との照合にしている。長い名前から照合し、部分一致を防ぐ。
_PREFECTURES = (
    "北海道",
    "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県",
    "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県",
    "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県",
    "沖縄県",
)
_PREFECTURES_BY_LENGTH = tuple(sorted(_PREFECTURES, key=len, reverse=True))


def extract_prefecture(title: Optional[str]) -> Optional[str]:
    """国土地理院の title から都道府県名を取り出す。

    title は必ず都道府県から始まる（`addressCode` は空で返るため使えない）。
    判別できない場合は None を返す（呼び出し側は通す判断をする）。
    """
    if not title:
        return None
    for prefecture in _PREFECTURES_BY_LENGTH:
        if title.startswith(prefecture):
            return prefecture
    return None


def is_supported_area(title: Optional[str]) -> bool:
    """対応エリア内か。都道府県を判別できない場合は True（誤って弾かない）。"""
    prefecture = extract_prefecture(title)
    if prefecture is None:
        return True
    return prefecture == SUPPORTED_PREFECTURE


def _within_japan(lat: float, lng: float) -> bool:
    return (
        JAPAN_LAT_RANGE[0] <= lat <= JAPAN_LAT_RANGE[1]
        and JAPAN_LNG_RANGE[0] <= lng <= JAPAN_LNG_RANGE[1]
    )


class GeocodingError(Exception):
    """ジオコーディングの失敗を表す例外（住所が見つからない場合は None を返す）。"""


class GeoPoint(TypedDict, total=False):
    lat: float
    lng: float
    # 以下は対応エリアの判定用。実装によっては付かない。
    prefecture: Optional[str]
    title: str


class Geocoder(ABC):
    """住所文字列を座標へ変換する抽象インターフェース。"""

    @abstractmethod
    def geocode(self, address: str) -> Optional[GeoPoint]:
        """住所を座標へ変換する。

        Returns:
            座標。住所に該当がなければ None。

        Raises:
            GeocodingError: 外部APIの障害や設定不備など、住所以前の問題。
        """
        raise NotImplementedError


class MockGeocoder(Geocoder):
    """ローカル辞書ベースのモック実装。

    `geocode_fixtures.json` の `entries` を部分一致(大文字小文字無視)で照合し、
    一致しない場合は `default` 座標へフォールバックする。
    **数件しか解決できない**ため、住所入力欄を使うなら azure_maps を選ぶこと。
    """

    def __init__(self, store: Optional[DataStore] = None) -> None:
        self._store = store or get_datastore()

    def geocode(self, address: str) -> Optional[GeoPoint]:
        if not address or not address.strip():
            return None

        fixtures = self._store.load_json(FIXTURE_RESOURCE)
        needle = address.strip().lower()

        for entry in fixtures.get("entries", []):
            match = str(entry.get("match", "")).lower()
            if match and match in needle:
                return {"lat": float(entry["lat"]), "lng": float(entry["lng"])}

        default = fixtures.get("default")
        if default:
            return {"lat": float(default["lat"]), "lng": float(default["lng"])}
        return None


class AzureMapsGeocoder(Geocoder):
    """Azure Maps Search API を使った実装。

    地図描画と同じサブスクリプションキーを使うため、追加の契約は不要。
    `requests` だけで完結させる（SDK を足してコールドスタートを重くしない）。
    """

    def __init__(self, subscription_key: str) -> None:
        self.subscription_key = subscription_key

    def geocode(self, address: str) -> Optional[GeoPoint]:
        query = (address or "").strip()
        if not query:
            # 空文字で外部APIを叩かない（無駄な課金とレート消費を避ける）
            return None

        if not self.subscription_key:
            raise GeocodingError(
                "Azure Maps のキーが未設定のため住所を検索できません"
            )

        import requests

        params: Dict[str, Any] = {
            "api-version": "1.0",
            "subscription-key": self.subscription_key,
            "query": query,
            # 国内に限定し日本語で返させる（同名地名の誤ヒットを減らす）
            "countrySet": "JP",
            "language": "ja-JP",
            "limit": 1,
        }

        try:
            response = requests.get(
                AZURE_MAPS_SEARCH_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            logger.error("Azure Maps Search がタイムアウトしました: %s", exc)
            raise GeocodingError("住所検索がタイムアウトしました") from exc
        except requests.RequestException as exc:
            logger.error("Azure Maps Search への接続に失敗しました: %s", exc)
            raise GeocodingError("住所検索サービスに接続できませんでした") from exc

        if response.status_code in (401, 403):
            logger.error("Azure Maps Search の認証に失敗しました (%s)", response.status_code)
            raise GeocodingError("住所検索の認証に失敗しました。キーを確認してください")
        if response.status_code == 429:
            logger.warning("Azure Maps Search のレート制限に達しました (429)")
            raise GeocodingError("住所検索が混み合っています。少し待って再試行してください")
        if not response.ok:
            logger.error(
                "Azure Maps Search がエラーを返しました: %s %s",
                response.status_code,
                response.text[:200],
            )
            raise GeocodingError("住所検索でエラーが発生しました")

        try:
            body = response.json()
        except ValueError as exc:
            logger.error("Azure Maps Search の応答を解釈できませんでした: %s", exc)
            raise GeocodingError("住所検索の応答を解釈できませんでした") from exc

        results = body.get("results") or []
        if not results:
            return None  # 住所が見つからないのはエラーではない

        position = results[0].get("position") or {}
        try:
            # Azure Maps は経度を "lon" で返す（"lng" ではない）
            return {"lat": float(position["lat"]), "lng": float(position["lon"])}
        except (KeyError, TypeError, ValueError):
            logger.error("Azure Maps Search の座標を解釈できませんでした: %r", position)
            return None


class GsiGeocoder(Geocoder):
    """国土地理院 住所検索API を使った実装。

    キー不要の公開API。日本の住所に特化しており、丁目・番地レベルまで
    解決できる。号（末尾の枝番）までは解決せず、番の代表点を返すため
    数百m〜1km程度の誤差が残る点に注意。

    応答は GeoJSON 形式で、**座標が [経度, 緯度] の順**で返る。
    """

    def geocode(self, address: str) -> Optional[GeoPoint]:
        query = (address or "").strip()
        if not query:
            return None

        import requests

        try:
            response = requests.get(
                GSI_SEARCH_URL,
                params={"q": query},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            logger.error("国土地理院APIがタイムアウトしました: %s", exc)
            raise GeocodingError("住所検索がタイムアウトしました") from exc
        except requests.RequestException as exc:
            logger.error("国土地理院APIへの接続に失敗しました: %s", exc)
            raise GeocodingError("住所検索サービスに接続できませんでした") from exc

        if response.status_code == 429:
            logger.warning("国土地理院APIのレート制限に達しました (429)")
            raise GeocodingError("住所検索が混み合っています。少し待って再試行してください")
        if not response.ok:
            logger.error(
                "国土地理院APIがエラーを返しました: %s %s",
                response.status_code,
                response.text[:200],
            )
            raise GeocodingError("住所検索でエラーが発生しました")

        try:
            body = response.json()
        except ValueError as exc:
            logger.error("国土地理院APIの応答を解釈できませんでした: %s", exc)
            raise GeocodingError("住所検索の応答を解釈できませんでした") from exc

        if not isinstance(body, list):
            logger.error("国土地理院APIの応答が想定外の形式です: %r", type(body))
            raise GeocodingError("住所検索の応答を解釈できませんでした")

        if not body:
            return None  # 該当なしはエラーではない

        try:
            # GeoJSON なので [経度, 緯度] の順。取り違えると日本国外に飛ぶ
            lng, lat = body[0]["geometry"]["coordinates"][:2]
            lat, lng = float(lat), float(lng)
        except (KeyError, IndexError, TypeError, ValueError):
            logger.error("国土地理院APIの座標を解釈できませんでした: %r", body[0])
            return None

        if not _within_japan(lat, lng):
            logger.error("国土地理院APIが国外の座標を返しました: %s, %s", lat, lng)
            return None

        # 対象エリアの判定は呼び出し側の責務。ここでは材料だけ返す。
        title = str(body[0].get("properties", {}).get("title", ""))
        return {
            "lat": lat,
            "lng": lng,
            "prefecture": extract_prefecture(title),
            "title": title,
        }


def get_geocoder() -> Geocoder:
    """環境変数に基づき Geocoder 実装を返すファクトリ。"""
    backend = config.geocoder_backend()

    if backend == "gsi":
        return GsiGeocoder()

    if backend == "mock":
        return MockGeocoder()

    if backend == "azure_maps":
        return AzureMapsGeocoder(config.azure_maps_subscription_key())

    raise ValueError(f"unknown GEOCODER_BACKEND: {backend!r}")
