"""`src/services/ratelimit.py` の単体テスト。

`/api/chat` は OpenAI の従量課金を消費するため、公開すると
「URLを知られたら請求が青天井」になる。これを防ぐのがレート制限。

二段構えにしている:
    1. IP単位     … 個々の利用者の使いすぎを抑える
    2. 全体の日次 … 分散アクセスでも1日の総額に上限を設ける

時刻は `time_fn` で注入し、sleep せずに窓の経過を検証する。
"""

from __future__ import annotations

import pytest

from src.services import ratelimit


class FakeClock:
    """テストから任意に進められる時計。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def make_limiter(clock, per_ip=3, window=3600, daily_global=10):
    return ratelimit.InMemoryRateLimiter(
        per_ip_limit=per_ip,
        per_ip_window_seconds=window,
        daily_global_limit=daily_global,
        time_fn=clock,
    )


# ---------------------------------------------------------------------------
# IP単位の制限
# ---------------------------------------------------------------------------

def test_allows_requests_within_limit(clock):
    limiter = make_limiter(clock, per_ip=3)
    for _ in range(3):
        assert limiter.check("1.2.3.4").allowed is True


def test_blocks_when_per_ip_limit_exceeded(clock):
    limiter = make_limiter(clock, per_ip=3)
    for _ in range(3):
        limiter.check("1.2.3.4")

    result = limiter.check("1.2.3.4")
    assert result.allowed is False
    assert result.scope == "ip"
    assert result.retry_after_seconds > 0


def test_limits_are_independent_per_ip(clock):
    """他人が使い切っても自分は使える。"""
    limiter = make_limiter(clock, per_ip=2)
    limiter.check("1.1.1.1")
    limiter.check("1.1.1.1")
    assert limiter.check("1.1.1.1").allowed is False
    assert limiter.check("2.2.2.2").allowed is True


def test_recovers_after_window_passes(clock):
    limiter = make_limiter(clock, per_ip=2, window=3600)
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4").allowed is False

    clock.advance(3601)
    assert limiter.check("1.2.3.4").allowed is True


def test_window_slides_rather_than_resetting(clock):
    """固定窓ではなく移動窓。窓の境目でまとめて撃たれるのを防ぐ。"""
    limiter = make_limiter(clock, per_ip=2, window=60)
    limiter.check("1.2.3.4")      # t=0
    clock.advance(50)
    limiter.check("1.2.3.4")      # t=50
    clock.advance(11)             # t=61 → 1件目だけ窓の外
    assert limiter.check("1.2.3.4").allowed is True   # 枠が1つ空く
    assert limiter.check("1.2.3.4").allowed is False  # もう空きなし


def test_retry_after_reflects_oldest_request(clock):
    limiter = make_limiter(clock, per_ip=1, window=600)
    limiter.check("1.2.3.4")
    clock.advance(100)
    result = limiter.check("1.2.3.4")
    assert result.allowed is False
    assert result.retry_after_seconds == pytest.approx(500, abs=1)


# ---------------------------------------------------------------------------
# 全体の日次上限（財布を守る最後の砦）
# ---------------------------------------------------------------------------

def test_blocks_when_daily_global_limit_exceeded(clock):
    """IPを変えられても、1日の総回数で頭打ちにする。"""
    limiter = make_limiter(clock, per_ip=100, daily_global=5)
    for i in range(5):
        assert limiter.check(f"10.0.0.{i}").allowed is True

    result = limiter.check("10.0.0.99")
    assert result.allowed is False
    assert result.scope == "global"


def test_daily_global_limit_resets_after_a_day(clock):
    limiter = make_limiter(clock, per_ip=100, daily_global=2)
    limiter.check("1.1.1.1")
    limiter.check("2.2.2.2")
    assert limiter.check("3.3.3.3").allowed is False

    clock.advance(86401)
    assert limiter.check("3.3.3.3").allowed is True


def test_rejected_requests_do_not_consume_quota(clock):
    """拒否したリクエストは LLM を呼ばないので、枠を消費させない。

    消費させると、連打された利用者が永久に回復しなくなる。
    """
    limiter = make_limiter(clock, per_ip=1, window=60)
    limiter.check("1.2.3.4")
    for _ in range(10):
        limiter.check("1.2.3.4")

    clock.advance(61)
    assert limiter.check("1.2.3.4").allowed is True


# ---------------------------------------------------------------------------
# 無効化
# ---------------------------------------------------------------------------

def test_zero_per_ip_means_unlimited(clock):
    """0 は「制限なし」。ローカル開発で邪魔にならないようにする。"""
    limiter = make_limiter(clock, per_ip=0, daily_global=0)
    for _ in range(50):
        assert limiter.check("1.2.3.4").allowed is True


def test_zero_daily_global_means_unlimited(clock):
    """全体上限だけを外せる（IP単位の制限は残る）。"""
    limiter = make_limiter(clock, per_ip=1, daily_global=0)
    for i in range(50):
        assert limiter.check(f"10.0.{i // 256}.{i % 256}").allowed is True


# ---------------------------------------------------------------------------
# クライアントIPの取り出し
# ---------------------------------------------------------------------------

def test_extracts_client_ip_from_x_forwarded_for():
    """Azure は X-Forwarded-For にポート付きで入れてくる。"""
    headers = {"X-Forwarded-For": "203.0.113.5:52001"}
    assert ratelimit.client_ip(headers) == "203.0.113.5"


def test_uses_leftmost_ip_when_proxied():
    """複数プロキシを経由すると連結される。左端が実クライアント。"""
    headers = {"X-Forwarded-For": "203.0.113.5, 70.41.3.18, 150.172.238.178"}
    assert ratelimit.client_ip(headers) == "203.0.113.5"


def test_falls_back_when_header_missing():
    """ヘッダが無くても落ちない。全員が同じ枠を共有する（安全側）。"""
    assert ratelimit.client_ip({}) == ratelimit.UNKNOWN_CLIENT


def test_header_lookup_is_case_insensitive():
    assert ratelimit.client_ip({"x-forwarded-for": "198.51.100.7"}) == "198.51.100.7"


def test_ipv6_is_kept_intact():
    """IPv6 はコロンを含むため、ポート除去で壊さない。"""
    headers = {"X-Forwarded-For": "2001:db8::1"}
    assert ratelimit.client_ip(headers) == "2001:db8::1"


# ---------------------------------------------------------------------------
# メモリ保護
# ---------------------------------------------------------------------------

def test_old_entries_are_evicted(clock):
    """IPごとに履歴を持つため、放置するとメモリが増え続ける。"""
    limiter = make_limiter(clock, per_ip=5, window=60)
    for i in range(200):
        limiter.check(f"10.1.{i // 256}.{i % 256}")

    clock.advance(61)
    limiter.check("192.0.2.1")
    assert limiter.tracked_clients() < 200
