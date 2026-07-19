"""アプリケーション設定。

Azure の資格情報・接続文字列は **一切ハードコードせず**、すべて環境変数
(`os.environ`) から読み込む。ローカル開発では以下の2経路で注入される。

  * `func start` 実行時 … `local.settings.json` の `Values`
  * それ以外(pytest / スクリプト) … プロジェクトルートの `.env`

ここに設定アクセスを集約することで、「ローカル(モック) → Azure(本番)」の
切り替えを環境変数だけで完結できるようにする。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# プロジェクトルート (このファイルは <root>/src/config.py にある)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 加工済みデータの既定ディレクトリ。
# `data/raw/` に置いた生データを scripts/ のクレンジングスクリプトが加工し、
# `data/processed/` へ出力する。アプリが読むのは常に processed 側。
DEFAULT_DATA_DIR = os.path.join("data", "processed")

DOTENV_PATH = os.path.join(PROJECT_ROOT, ".env")


def load_dotenv(path: str = DOTENV_PATH) -> None:
    """`.env` を読み込んで環境変数へ反映する。

    python-dotenv に依存すると Azure Functions のランタイム依存が増える
    （コールドスタート対策のため避けたい）ので、必要最小限の実装を自前で持つ。

    仕様:
      * `KEY=VALUE` 形式のみを解釈する
      * `#` で始まる行と空行は無視する
      * 値を囲むシングル/ダブルクォートは取り除く
      * **既に設定済みの環境変数は上書きしない**
        （`func start` の local.settings.json や CI の設定を優先するため）
    """
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as fp:
            for raw_line in fp:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        # 設定ファイルが読めなくてもアプリは既定値で動作させる
        logger.warning(".env の読み込みに失敗しました: %s (%s)", path, exc)


# import 時に一度だけ読み込む
load_dotenv()


def _get(name: str, default: str) -> str:
    """環境変数を取得する。未設定・空文字なら default を返す。"""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


# --- データストア (現在: ローカルファイル / 将来: Azure Blob Storage) ---
def data_store_backend() -> str:
    """使用するデータストア実装。'local' | 'blob'"""
    return _get("DATA_STORE_BACKEND", "local").lower()


def local_data_dir() -> str:
    """加工済みデータ(JSON)を格納したディレクトリの絶対パス。

    相対パスが指定された場合はプロジェクトルート基準で解決する。
    既定値は `data/processed`（`scripts/` 配下の取得スクリプトの出力先）。
    テストでは環境変数 `LOCAL_DATA_DIR` で `tests/fixtures` に差し替える。
    """
    raw = _get("LOCAL_DATA_DIR", DEFAULT_DATA_DIR)
    if os.path.isabs(raw):
        return raw
    return os.path.join(PROJECT_ROOT, raw)


# --- Azure Blob Storage (将来利用。現在は空でよい) ---
def blob_connection_string() -> str:
    return _get("BLOB_CONNECTION_STRING", "")


def blob_container_name() -> str:
    return _get("BLOB_CONTAINER_NAME", "opendata")


# --- ジオコーダ (現在: モック / 将来: Azure Maps Search API) ---
def geocoder_backend() -> str:
    """使用するジオコーダ実装。'gsi' | 'azure_maps' | 'mock'

    既定は国土地理院（キー不要・国内住所に強い）。Azure Maps は
    日本の住所をほぼ解決できなかったため既定から外している。
    """
    return _get("GEOCODER_BACKEND", "gsi").lower()


# --- Azure Maps ---
def azure_maps_subscription_key() -> str:
    """Azure Maps の主キー（サブスクリプションキー）。

    未設定なら空文字を返す。呼び出し側はキー未設定でも落ちないこと。
    旧名 `AZURE_MAPS_KEY` も後方互換のため参照する。
    """
    return _get("AZURE_MAPS_SUBSCRIPTION_KEY", "") or _get("AZURE_MAPS_KEY", "")


# 旧名の別名（既存コード互換）
def azure_maps_key() -> str:
    return azure_maps_subscription_key()


# --- 生成AI (LLM) ---
def llm_backend() -> str:
    """使用する LLM 実装。'openai' | 'azure_openai' | 'echo'

    既定は OpenAI API（api.openai.com）。Azure OpenAI はデプロイ単位の
    クォータ管理が必要なため、素の OpenAI API を既定にしている。
    'echo' は外部通信しないスタブ。テストとキー未設定時の動作確認に使う。
    """
    return _get("LLM_BACKEND", "openai").lower()


# --- OpenAI API (api.openai.com) ---
def openai_api_key() -> str:
    return _get("OPENAI_API_KEY", "")


def openai_model() -> str:
    """使用するモデル名。Azure と違いデプロイ名ではなくモデル名を直接指定する。"""
    return _get("OPENAI_MODEL", "gpt-4o-mini")


def openai_base_url() -> str:
    """API のベースURL。互換エンドポイントを使う場合に差し替える。"""
    return _get("OPENAI_BASE_URL", "https://api.openai.com/v1")


def azure_openai_endpoint() -> str:
    """例: https://<リソース名>.openai.azure.com"""
    return _get("AZURE_OPENAI_ENDPOINT", "")


def azure_openai_api_key() -> str:
    return _get("AZURE_OPENAI_API_KEY", "")


def azure_openai_deployment() -> str:
    """Azure OpenAI の *デプロイ名*（モデル名ではない）。"""
    return _get("AZURE_OPENAI_DEPLOYMENT", "")


def azure_openai_api_version() -> str:
    return _get("AZURE_OPENAI_API_VERSION", "2024-10-21")


# --- AI アシスタントの検索範囲 ---
def chat_search_radius_km() -> float:
    """AIに渡す周辺施設の検索半径(km)。広げるとトークン量と応答時間が増える。"""
    try:
        return float(_get("CHAT_SEARCH_RADIUS_KM", "2.0"))
    except ValueError:
        return 2.0


# --- AI アシスタントのレート制限 ---
#
# チャットは OpenAI の従量課金を消費するため、認証なしで公開する以上
# 上限が必要。いずれも 0 を指定すると無制限（ローカル開発用）。
# 既定値は「公開しても財布が痛まない」側に倒してある。
def _positive_int(name: str, default: int) -> int:
    """環境変数を非負整数として読む。不正な値は既定値に倒す。

    設定ミスで制限が外れる（= 課金が青天井になる）ほうが、
    厳しすぎる制限より損害が大きいため、例外時は既定値を採用する。
    """
    raw = _get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s の値が不正です (%r)。既定値 %s を使います。", name, raw, default)
        return default
    if value < 0:
        logger.warning("%s に負の値が指定されました (%s)。既定値 %s を使います。", name, value, default)
        return default
    return value


def chat_rate_limit_per_ip() -> int:
    """1つのIPが窓あたりに送れる質問数。0 で無制限。"""
    return _positive_int("CHAT_RATE_LIMIT_PER_IP", 10)


def chat_rate_limit_window_seconds() -> int:
    """上記を数える窓の長さ(秒)。既定は1時間。"""
    return _positive_int("CHAT_RATE_LIMIT_WINDOW_SECONDS", 3600)


def chat_rate_limit_daily_global() -> int:
    """全利用者合計の1日あたり質問数。0 で無制限。

    IPを変えられても効く、課金に対する最後の砦。
    """
    return _positive_int("CHAT_RATE_LIMIT_DAILY_GLOBAL", 200)
