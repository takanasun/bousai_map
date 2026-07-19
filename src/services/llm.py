"""LLM（生成AI）クライアントの抽象化。

`DataStore` / `Geocoder` と同じ方針で、実装を差し替え可能にしておく。
資格情報は環境変数からのみ読み込み、ソースにハードコードしない。

    LLM_BACKEND=openai        … OpenAI API（既定）
    LLM_BACKEND=azure_openai  … Azure OpenAI Service
    LLM_BACKEND=echo          … 外部通信しないスタブ（テスト/キー未設定時）

OpenAI と Azure OpenAI はどちらも chat/completions 形式で、違いは
エンドポイント・認証ヘッダ・モデル指定方法だけ。共通処理は
`_ChatCompletionsClient` に集約し、差分だけをサブクラスで表現する。

キーが未設定でもアプリは落ちない。`is_configured()` が False を返し、
呼び出し側（/api/chat）が案内メッセージを出す。Azure Maps と同じ扱い。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .. import config

logger = logging.getLogger(__name__)

# 外部API呼び出しのタイムアウト（秒）。仕様6の応答時間要件に配慮する。
REQUEST_TIMEOUT_SECONDS = 30

# 応答の最大トークン数。長すぎる回答はチャット欄で読みにくく、コストも増える。
MAX_RESPONSE_TOKENS = 800


class LLMError(Exception):
    """LLM 呼び出しの失敗を表す例外。"""


class LLMClient(ABC):
    """自然言語の問い合わせに答えるクライアントの抽象インターフェース。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """資格情報が揃っていて実際に呼び出せるか。"""
        raise NotImplementedError

    @abstractmethod
    def configuration_hint(self) -> str:
        """未設定時に利用者へ見せる、設定すべき項目の案内。"""
        raise NotImplementedError

    @abstractmethod
    def complete(self, system_prompt: str, user_message: str) -> str:
        """システムプロンプトと質問を渡し、回答テキストを返す。

        Raises:
            LLMError: 呼び出しに失敗した場合。
        """
        raise NotImplementedError


class EchoLLMClient(LLMClient):
    """外部通信しないスタブ。

    テストと「キー未設定だが動作確認したい」場面で使う。
    与えられたコンテキストから機械的に応答を返すだけで、推論はしない。
    """

    def is_configured(self) -> bool:
        return True

    def configuration_hint(self) -> str:
        return ""

    def complete(self, system_prompt: str, user_message: str) -> str:
        return (
            "【スタブ応答】AIサービスが未設定のため、実際の生成AIは呼び出していません。\n"
            f"ご質問: {user_message}\n"
            "地図上のピンと絞り込み機能はそのままお使いいただけます。"
        )


class _ChatCompletionsClient(LLMClient):
    """OpenAI 互換の chat/completions を呼ぶ共通実装。

    `openai` パッケージを追加せず `requests` だけで完結させる
    （Azure Functions のコールドスタートを重くしないため）。
    """

    #: ログとエラーメッセージに出すサービス名
    service_name = "AIサービス"

    @abstractmethod
    def _url(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _headers(self) -> Dict[str, str]:
        raise NotImplementedError

    def _payload(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        return {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": MAX_RESPONSE_TOKENS,
            "temperature": 0.2,
        }

    def complete(self, system_prompt: str, user_message: str) -> str:
        if not self.is_configured():
            raise LLMError(f"{self.service_name} の設定が不足しています")

        # requests は関数内で import する（未設定時に import コストを払わない）
        import requests

        try:
            response = requests.post(
                self._url(),
                headers=self._headers(),
                json=self._payload(system_prompt, user_message),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            logger.error("%s がタイムアウトしました: %s", self.service_name, exc)
            raise LLMError("AIの応答がタイムアウトしました") from exc
        except requests.RequestException as exc:
            logger.error("%s への接続に失敗しました: %s", self.service_name, exc)
            raise LLMError("AIサービスに接続できませんでした") from exc

        if response.status_code == 401:
            logger.error("%s の認証に失敗しました (401)", self.service_name)
            raise LLMError("AIサービスの認証に失敗しました。APIキーを確認してください")
        if response.status_code == 429:
            logger.warning("%s のレート制限に達しました (429)", self.service_name)
            raise LLMError("AIサービスが混み合っています。少し待って再試行してください")
        if not response.ok:
            logger.error(
                "%s がエラーを返しました: %s %s",
                self.service_name,
                response.status_code,
                response.text[:200],
            )
            raise LLMError("AIサービスでエラーが発生しました")

        try:
            body = response.json()
            return body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.error("%s の応答を解釈できませんでした: %s", self.service_name, exc)
            raise LLMError("AIの応答を解釈できませんでした") from exc


class OpenAIClient(_ChatCompletionsClient):
    """OpenAI API (api.openai.com) を呼び出す実装。

    Azure と違いデプロイの用意が不要で、モデル名をリクエストボディで指定する。
    """

    service_name = "OpenAI"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    def configuration_hint(self) -> str:
        return ".env の OPENAI_API_KEY を設定してください。"

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        payload = super()._payload(system_prompt, user_message)
        # OpenAI はモデル名をボディで指定する（Azure は URL のデプロイ名で指定）
        payload["model"] = self.model
        return payload


class AzureOpenAIClient(_ChatCompletionsClient):
    """Azure OpenAI Service を呼び出す実装。

    モデルは URL のデプロイ名で決まるため、ボディに model を入れない。
    """

    service_name = "Azure OpenAI"

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment)

    def configuration_hint(self) -> str:
        return (
            ".env の AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / "
            "AZURE_OPENAI_DEPLOYMENT を設定してください。"
        )

    def _url(self) -> str:
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _headers(self) -> Dict[str, str]:
        # Azure は Bearer ではなく api-key ヘッダを使う
        return {"api-key": self.api_key, "Content-Type": "application/json"}


def get_llm_client() -> LLMClient:
    """環境変数に基づき LLM クライアントを返すファクトリ。"""
    backend = config.llm_backend()

    if backend == "echo":
        return EchoLLMClient()

    if backend == "openai":
        return OpenAIClient(
            api_key=config.openai_api_key(),
            model=config.openai_model(),
            base_url=config.openai_base_url(),
        )

    if backend == "azure_openai":
        return AzureOpenAIClient(
            endpoint=config.azure_openai_endpoint(),
            api_key=config.azure_openai_api_key(),
            deployment=config.azure_openai_deployment(),
            api_version=config.azure_openai_api_version(),
        )

    raise LLMError(f"unknown LLM_BACKEND: {backend!r}")


# --- プロンプト組み立て ------------------------------------------------------

# 災害種別の内部キー → 日本語ラベル。
# キーは `scripts/clean_shelters.py` の DISASTER_COLUMNS の値と一致させること
# （ずれると英語キーがそのまま利用者の目に触れる）。
DISASTER_LABELS = {
    "flood": "洪水",
    "landslide": "崖崩れ・土石流・地滑り",
    "stormSurge": "高潮",
    "earthquake": "地震",
    "tsunami": "津波",
    "fire": "大規模な火事",
    "inlandFlood": "内水氾濫",
    "volcano": "火山現象",
}


def _disaster_label(key: str) -> str:
    """内部キーを日本語に。未知のキーはそのまま返す（将来の追加で落ちない）。"""
    return DISASTER_LABELS.get(key, key)

SYSTEM_PROMPT = """あなたは防災マップアプリの案内アシスタントです。

以下のルールを必ず守ってください。
1. 回答は必ず「参考データ」に書かれている情報だけに基づいてください。
   データに無いことは推測せず「データに含まれていません」と答えてください。
2. 避難所には2種類あり、混同すると命に関わります。必ず区別してください。
   - 指定緊急避難場所: 切迫した危険から「命を守る」ために逃げ込む場所。災害種別ごとの指定。
   - 指定避難所: 自宅に戻れない人が一定期間「生活する」場所。
3. 距離は「参考データ」の距離(km)をそのまま使い、自分で計算し直さないでください。
4. 日本語で、簡潔に答えてください。箇条書きを活用してください。
5. 医療に関する判断（診断・治療方針）はせず、施設の案内に留めてください。
6. 「地価が安く避難所が近い場所」を聞かれたら、「エリア別の地価と防災施設」の
   表を使って答えてください。地価と施設数は既に集計済みなので、自分で
   平均や合計を計算し直さないでください。
   おすすめを挙げるときは必ず「地価(円/㎡)」と「徒歩圏の施設数」の両方を示し、
   なぜその組み合わせが良いのかを一言添えてください。
7. 地価は不動産取引価格ではなく、国土交通省の地価公示による「地点」の目安です。
   同じ町でも場所により差があることを一言添えてください。
8. 安全性を地価だけで判断しないでください。地価が安い場所は工業地帯や
   浸水想定区域であることがあり、対応災害の種別も併せて確認するよう促してください。
"""


def build_context(
    origin: Dict[str, float],
    shelters: List[Dict[str, Any]],
    hospitals: List[Dict[str, Any]],
    toilets: List[Dict[str, Any]],
    areas: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """周辺施設を LLM に渡すテキストコンテキストに整形する。

    トークン量を抑えるため、必要な項目だけを絞って渡す。
    """
    lines: List[str] = []
    lines.append(f"# 基準地点\n緯度 {origin['lat']}, 経度 {origin['lng']}\n")

    lines.append("# 周辺の避難所")
    if shelters:
        for s in shelters:
            roles = []
            if s.get("isEmergencySite"):
                roles.append("指定緊急避難場所(命を守る)")
            if s.get("isEvacuationCenter"):
                roles.append("指定避難所(生活する)")
            if s.get("isWelfareShelter"):
                roles.append("福祉避難所")
            disasters = [_disaster_label(d) for d in (s.get("disasterTypes") or [])]
            lines.append(
                f"- {s.get('name')} / {' + '.join(roles) or '区分不明'} / "
                f"距離 {s.get('distanceKm')}km / 対応災害: {'、'.join(disasters) or 'なし'}"
            )
    else:
        lines.append("- （範囲内に該当なし）")

    lines.append("\n# 周辺の医療機関")
    if hospitals:
        for h in hospitals:
            caps = h.get("capabilities") or []
            lines.append(
                f"- {h.get('name')} / 距離 {h.get('distanceKm')}km / "
                f"診療科: {','.join(caps[:12]) or '不明'}"
            )
    else:
        lines.append("- （範囲内に該当なし）")

    lines.append("\n# 周辺の公衆トイレ")
    if toilets:
        for t in toilets:
            attrs = t.get("attributes") or {}
            features = []
            if attrs.get("accessible"):
                features.append("車椅子対応")
            if attrs.get("ostomate"):
                features.append("オストメイト対応")
            if attrs.get("open24h"):
                features.append("24時間")
            lines.append(
                f"- {t.get('name')} / 距離 {t.get('distanceKm')}km / "
                f"{','.join(features) or '設備情報なし'}"
            )
    else:
        lines.append("- （範囲内に該当なし）")

    if areas:
        lines.append("\n# エリア別の地価と防災施設（町名ごと・地価の安い順）")
        lines.append("※ 数値は集計済み。再計算しないこと。")
        for a in areas:
            lines.append(
                f"- {a.get('town')} / 地価 平均{a.get('avgPricePerSqm'):,}円/㎡"
                f"（{a.get('minPricePerSqm'):,}〜{a.get('maxPricePerSqm'):,}） / "
                f"半径{a.get('radiusKm')}km内 避難所{a.get('sheltersNearby')}件・"
                f"医療機関{a.get('hospitalsNearby')}件 / 調査地点{a.get('landPoints')}件"
            )

    return "\n".join(lines)


def build_user_message(question: str, context: str) -> str:
    """質問と参考データを1つのユーザーメッセージにまとめる。"""
    return f"# 参考データ\n{context}\n\n# 質問\n{question}"
