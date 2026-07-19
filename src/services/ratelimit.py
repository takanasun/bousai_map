"""`/api/chat` のレート制限。

なぜ必要か:
    チャットは OpenAI の従量課金を消費する。認証なしで公開すると、
    URL を知られただけで請求が青天井になる。クローラや悪意ある連打は
    「起こるかもしれない」ではなく「起こる」前提で設計する。

二段構え:
    1. IP単位の移動窓 … 個々の利用者の使いすぎを抑える
    2. 全体の日次上限 … IP を変えられても1日の総額に天井を作る

制約（重要）:
    カウンタはプロセス内メモリに持つ。Functions が複数インスタンスへ
    スケールアウトすると、インスタンスごとに別のカウンタになり、
    実効的な上限がインスタンス数倍に緩む。
    このため公開時は Function App のスケール上限を 1 に固定する
    （デプロイ手順参照）。本番規模で使うなら Redis や Table Storage の
    共有カウンタへ差し替える前提で、`RateLimiter` を抽象にしてある。

    加えて、コード側の制限はあくまで一次防衛でしかない。
    最終的な財布の保護は OpenAI 側の利用上限設定で行う。
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# X-Forwarded-For が取れなかったクライアントの識別子。
# 全員がこの1枠を共有するため、匿名アクセスは自動的に強く制限される。
UNKNOWN_CLIENT = "unknown"

SECONDS_PER_DAY = 86400

# 古いエントリの掃除間隔（秒）。毎リクエスト全走査すると無駄なので間引く。
PRUNE_INTERVAL_SECONDS = 60


def client_ip(headers: Mapping[str, str]) -> str:
    """リクエストヘッダからクライアントIPを取り出す。

    Azure Functions はロードバランサ越しに受けるため `REMOTE_ADDR` は
    使えず、`X-Forwarded-For` を見る。Azure は末尾にポートを付けてくる
    （例: `203.0.113.5:52001`）ので剥がす。

    プロキシを複数経由すると `client, proxy1, proxy2` と連結されるため
    左端を採用する。左端は詐称可能だが、詐称してまで使う相手には
    全体の日次上限のほうで対処する。
    """
    value = ""
    for name, raw in headers.items():
        if name.lower() == "x-forwarded-for":
            value = str(raw or "")
            break

    first = value.split(",")[0].strip()
    if not first:
        return UNKNOWN_CLIENT

    # IPv6 はコロンを複数含むため、ポート除去で壊さないよう分岐する
    if first.startswith("["):
        # [2001:db8::1]:52001 形式
        return first[1:].split("]")[0]
    if first.count(":") == 1:
        return first.split(":")[0]
    return first


@dataclass(frozen=True)
class RateLimitResult:
    """レート制限の判定結果。

    Attributes:
        allowed: 通してよいか。
        scope: 拒否した理由の種別。"ip" か "global"。許可時は None。
        retry_after_seconds: 再試行までの目安秒数。HTTP の Retry-After に使う。
    """

    allowed: bool
    scope: Optional[str] = None
    retry_after_seconds: int = 0


class RateLimiter(ABC):
    """レート制限の抽象インターフェース。

    共有ストア（Redis 等）を使う実装へ差し替えられるようにしている。
    """

    @abstractmethod
    def check(self, client: str) -> RateLimitResult:
        """1回分を消費して判定する。拒否した場合は消費しない。"""
        raise NotImplementedError


class InMemoryRateLimiter(RateLimiter):
    """プロセス内メモリで数える移動窓レート制限。

    Args:
        per_ip_limit: IPごとの上限回数。0 なら無制限。
        per_ip_window_seconds: 上記を数える窓の長さ（秒）。
        daily_global_limit: 全体の1日あたり上限。0 なら無制限。
        time_fn: 現在時刻を返す関数。テストから注入する。
    """

    def __init__(
        self,
        per_ip_limit: int,
        per_ip_window_seconds: int,
        daily_global_limit: int,
        time_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        if time_fn is None:
            import time

            time_fn = time.monotonic

        self._per_ip_limit = max(0, int(per_ip_limit))
        self._window = max(1, int(per_ip_window_seconds))
        self._daily_limit = max(0, int(daily_global_limit))
        self._time = time_fn

        self._hits: Dict[str, Deque[float]] = {}
        self._day_start = time_fn()
        self._day_count = 0
        self._last_prune = time_fn()

        # Functions のワーカーはスレッドで並行実行されうる
        self._lock = threading.Lock()

    def check(self, client: str) -> RateLimitResult:
        with self._lock:
            now = self._time()
            self._maybe_prune(now)

            # --- 全体の日次上限を先に見る（財布の保護が最優先） ---
            if now - self._day_start >= SECONDS_PER_DAY:
                self._day_start = now
                self._day_count = 0

            if self._daily_limit and self._day_count >= self._daily_limit:
                remaining = self._day_start + SECONDS_PER_DAY - now
                logger.warning(
                    "全体の日次上限に達しました (%s回/日)。以降は翌日まで拒否します。",
                    self._daily_limit,
                )
                return RateLimitResult(False, "global", max(1, int(remaining)))

            # --- IP単位の移動窓 ---
            if self._per_ip_limit:
                history = self._hits.setdefault(client, deque())
                cutoff = now - self._window
                while history and history[0] <= cutoff:
                    history.popleft()

                if len(history) >= self._per_ip_limit:
                    # 拒否したぶんは記録しない。記録すると連打した利用者の
                    # 窓が永久に埋まり続け、いつまでも回復しなくなる。
                    remaining = history[0] + self._window - now
                    return RateLimitResult(False, "ip", max(1, int(remaining)))

                history.append(now)

            self._day_count += 1
            return RateLimitResult(True)

    def tracked_clients(self) -> int:
        """保持しているクライアント数。メモリ使用量の監視・テスト用。"""
        with self._lock:
            return len(self._hits)

    def _maybe_prune(self, now: float) -> None:
        """窓を過ぎた履歴を捨てる。呼び出し側でロック済みであること。

        IP ごとに履歴を持つため、掃除しないと長時間稼働で増え続ける。
        毎回の全走査は無駄なので間引く。
        """
        if now - self._last_prune < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now

        cutoff = now - self._window
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._hits[key]


_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """環境変数に基づく RateLimiter を返す（プロセス内で使い回す）。

    リクエストごとに作り直すとカウンタが毎回リセットされ、
    制限がまったく効かなくなるため必ず共有する。
    """
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                from .. import config

                _limiter = InMemoryRateLimiter(
                    per_ip_limit=config.chat_rate_limit_per_ip(),
                    per_ip_window_seconds=config.chat_rate_limit_window_seconds(),
                    daily_global_limit=config.chat_rate_limit_daily_global(),
                )
    return _limiter


def reset_rate_limiter() -> None:
    """共有インスタンスを捨てる。テストが設定を変えて作り直すために使う。"""
    global _limiter
    with _limiter_lock:
        _limiter = None
