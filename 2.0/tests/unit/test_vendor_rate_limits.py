"""Yahoo (yfinance) and Twelve Data: one gate per vendor per process.

Found in the 2026-09-13 audit (3.10): neither vendor had a limiter or a backoff.
Twelve Data's free plan is 8 credits a minute and 800 a day, and every script
that called it paced itself (record_moves 7.5 s, backfill_historical 8 s) or not
at all (TACO's fallback). yfinance calls were bare -- a scan, the calendar sync's
Yahoo fallback and the bias validator looped over tickers with no spacing, and a
YFRateLimitError was logged as a failure for that ticker and the loop went on
into the same limit.

A VendorGate is the Tradier limiter's sliding window plus a backoff every caller
shares. These tests run on a fake clock: nothing here sleeps.
"""

import subprocess
import sys
import threading
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

import pytest

from src.utils import rate_limiter  # noqa: E402
from src.utils.rate_limiter import (  # noqa: E402
    TWELVE_DATA,
    YAHOO,
    DailyCallCounter,
    VendorDailyLimit,
    VendorRateLimited,
    create_twelve_data_gate,
    create_yahoo_gate,
    vendor_gate,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0)


class Throttled(Exception):
    def __init__(self, retry_after=0.0):
        super().__init__(f"slow down ({retry_after})")
        self.retry_after = retry_after


def _throttle_hint(e):
    return e.retry_after if isinstance(e, Throttled) else None


def _max_in_any_window(stamps, width):
    worst, j = 0, 0
    for i, t in enumerate(stamps):
        while stamps[j] <= t - width:
            j += 1
        worst = max(worst, i - j + 1)
    return worst


def _stamp(clock, stamps, result="ok"):
    def call():
        stamps.append(clock.now)
        return result
    return call


def test_twelve_data_never_more_than_8_calls_in_any_60_seconds():
    clock = FakeClock()
    gate = create_twelve_data_gate(clock=clock.time, sleep=clock.sleep)
    stamps = []
    for _ in range(100):
        gate.call(_stamp(clock, stamps), _throttle_hint)
        clock.sleep(0.5)                       # a call takes half a second

    assert _max_in_any_window(stamps, 60.0) <= 8
    # A limit, not a slowdown: the first 8 go at once.
    assert stamps[7] - stamps[0] < 5.0


def test_yahoo_never_more_than_one_call_in_any_second():
    clock = FakeClock()
    gate = create_yahoo_gate(clock=clock.time, sleep=clock.sleep)
    stamps = []
    for _ in range(50):
        gate.call(_stamp(clock, stamps), _throttle_hint)
        clock.sleep(0.05)

    assert _max_in_any_window(stamps, 1.0) <= 1
    assert stamps[-1] - stamps[0] < 50.0


def test_a_retry_waits_for_the_vendors_retry_after():
    clock = FakeClock()
    gate = create_twelve_data_gate(clock=clock.time, sleep=clock.sleep)
    stamps = []

    def answer():
        stamps.append(clock.now)
        if len(stamps) == 1:
            raise Throttled(retry_after=7.0)
        return "bars"

    assert gate.call(answer, _throttle_hint) == "bars"
    assert stamps[1] - stamps[0] == pytest.approx(7.0)


def test_without_a_retry_after_the_wait_doubles():
    clock = FakeClock()
    gate = create_yahoo_gate(clock=clock.time, sleep=clock.sleep)
    stamps = []

    def answer():
        stamps.append(clock.now)
        if len(stamps) < 3:
            raise Throttled(retry_after=0.0)
        return "closes"

    assert gate.call(answer, _throttle_hint) == "closes"
    assert [round(b - a, 6) for a, b in pairwise(stamps)] == [
        gate.base_delay, 2 * gate.base_delay]


@pytest.mark.parametrize("factory", [create_yahoo_gate, create_twelve_data_gate])
def test_a_limit_that_never_clears_gives_up_within_the_budget(factory):
    clock = FakeClock()
    gate = factory(clock=clock.time, sleep=clock.sleep)
    stamps = []

    def answer():
        stamps.append(clock.now)
        raise Throttled(retry_after=0.0)

    start = clock.now
    with pytest.raises(Throttled):
        gate.call(answer, _throttle_hint)

    assert len(stamps) <= gate.attempts
    assert clock.now - start <= gate.budget


def test_another_error_is_raised_at_once_and_never_retried():
    clock = FakeClock()
    gate = create_twelve_data_gate(clock=clock.time, sleep=clock.sleep)
    stamps = []

    def answer():
        stamps.append(clock.now)
        raise ValueError("symbol not found")

    with pytest.raises(ValueError):
        gate.call(answer, _throttle_hint)
    assert len(stamps) == 1


def test_the_backoff_is_shared_so_the_next_caller_waits_instead_of_spending_its_retries():
    clock = FakeClock()
    gate = create_yahoo_gate(clock=clock.time, sleep=clock.sleep)
    first = []

    def blocked():
        first.append(clock.now)
        raise Throttled()

    with pytest.raises(Throttled):
        gate.call(blocked, _throttle_hint)
    failed_at = clock.now

    second = []
    assert gate.call(_stamp(clock, second, "open again"), _throttle_hint) == "open again"
    # The first caller's last rate limit paused the gate; the second caller's
    # one request waited it out rather than hitting the same limit.
    assert second[0] - failed_at >= gate.base_delay * 2 ** (gate.attempts - 1)

    # A success clears the strikes: the next rate limit starts from the base delay.
    third = []

    def once():
        third.append(clock.now)
        if len(third) == 1:
            raise Throttled()
        return "ok"

    gate.call(once, _throttle_hint)
    assert third[1] - third[0] == pytest.approx(gate.base_delay)


def test_a_pause_longer_than_the_budget_fails_fast_without_asking():
    """While the vendor keeps refusing, later callers do not each sit out a
    budget of retries -- the scan that loops over 100 tickers must not hang."""
    clock = FakeClock()
    gate = create_yahoo_gate(clock=clock.time, sleep=clock.sleep)

    def blocked():
        raise Throttled()

    for _ in range(3):
        try:
            gate.call(blocked, _throttle_hint)
        except (Throttled, VendorRateLimited):
            pass

    asked = []
    before = clock.now
    with pytest.raises(VendorRateLimited):
        gate.call(_stamp(clock, asked), _throttle_hint)
    assert asked == []
    assert clock.now - before < 1.0


def test_one_gate_per_vendor_per_process(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_gates", {})
    assert vendor_gate(YAHOO) is vendor_gate(YAHOO)
    assert vendor_gate(TWELVE_DATA) is vendor_gate(TWELVE_DATA)
    assert vendor_gate(YAHOO) is not vendor_gate(TWELVE_DATA)
    assert (vendor_gate(TWELVE_DATA).limiter.max_calls,
            vendor_gate(TWELVE_DATA).limiter.per_seconds) == (8, 60.0)


# --- Twelve Data's day: one count for every process on the host ---------------------
# The Basic plan's 800 credits reset at 00:00 UTC (support.twelvedata.com/en/articles/
# 5615854-credits). Until 2026-09-14 only record_moves --budget counted calls, and
# only its own: the bot's auto-backfill, a /maintenance backfill and TACO's fallback
# spent from the same 800 unseen.

def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


class WallClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_the_day_refuses_at_the_ceiling_and_starts_again_at_utc_midnight(tmp_path):
    wall = WallClock(_utc(2026, 9, 14, 23, 59, 58))
    counter = DailyCallCounter(tmp_path / "calls", ceiling=3, now=wall)
    assert [counter.take() for _ in range(4)] == [True, True, True, False]
    assert counter.used() == 3

    wall.t = _utc(2026, 9, 15, 0, 0, 0)        # 20:00 on the 14th in New York
    assert counter.used() == 0
    assert counter.take() is True
    assert counter.used() == 1
    assert (tmp_path / "calls").read_text().split() == ["2026-09-15", "1"]


def test_every_counter_on_the_file_shares_one_count(tmp_path):
    wall = WallClock(_utc(2026, 9, 14, 12, 0, 0))
    bot = DailyCallCounter(tmp_path / "state" / "calls", ceiling=5, now=wall)
    job = DailyCallCounter(tmp_path / "state" / "calls", ceiling=5, now=wall)
    assert [bot.take() for _ in range(3)] == [True, True, True]
    assert [job.take() for _ in range(3)] == [True, True, False]
    assert bot.used() == job.used() == 5


def test_a_corrupt_record_counts_from_zero_rather_than_blocking_the_day(tmp_path):
    path = tmp_path / "calls"
    path.write_text("not a count at all, and longer than the record\n")
    counter = DailyCallCounter(path, ceiling=2, now=WallClock(_utc(2026, 9, 14, 12, 0, 0)))
    assert counter.take() is True
    assert counter.used() == 1


# Imports first, then waits on stdin so every process starts taking at once.
_TAKE_IN_A_PROCESS = """
import sys
from src.utils.rate_limiter import DailyCallCounter
counter = DailyCallCounter(sys.argv[1], ceiling=int(sys.argv[2]))
print("ready", flush=True)
sys.stdin.readline()
print(sum(counter.take() for _ in range(int(sys.argv[3]))), flush=True)
"""


@pytest.mark.parametrize("ceiling, granted", [(10_000, 2400), (1000, 1000)])
def test_concurrent_processes_lose_no_count_and_never_pass_the_ceiling(tmp_path, ceiling, granted):
    """Six processes taking 400 each at the same moment, as the bot, record_moves
    and a terminal backfill would: every take counted when the ceiling is out of
    reach, and exactly the ceiling granted when it is not."""
    path = tmp_path / "calls"
    procs = [subprocess.Popen([sys.executable, "-c", _TAKE_IN_A_PROCESS, str(path), str(ceiling), "400"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
             for _ in range(6)]
    assert [p.stdout.readline().strip() for p in procs] == ["ready"] * 6
    for p in procs:
        p.stdin.write("go\n")
        p.stdin.flush()
    took = [int(p.communicate(timeout=60)[0]) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(took) == granted
    assert DailyCallCounter(path, ceiling=ceiling).used() == granted


def test_concurrent_threads_lose_no_count(tmp_path):
    counter = DailyCallCounter(tmp_path / "calls", ceiling=10_000)
    start = threading.Barrier(8)

    def take():
        start.wait()
        for _ in range(200):
            counter.take()

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter.used() == 1600


def test_a_gate_past_the_days_ceiling_refuses_without_asking(tmp_path):
    clock = FakeClock()
    counter = DailyCallCounter(tmp_path / "calls", ceiling=2, now=WallClock(_utc(2026, 9, 14, 12, 0, 0)))
    gate = create_twelve_data_gate(clock=clock.time, sleep=clock.sleep, daily=counter)
    stamps = []
    for _ in range(2):
        gate.call(_stamp(clock, stamps), _throttle_hint)

    with pytest.raises(VendorDailyLimit) as err:
        gate.call(_stamp(clock, stamps), _throttle_hint)
    assert isinstance(err.value, VendorRateLimited)     # every call site's existing path
    assert len(stamps) == 2
    assert "2 calls" in str(err.value) and "2026-09-14 UTC" in str(err.value)


def test_every_request_counts_against_the_day_retries_included(tmp_path):
    clock = FakeClock()
    counter = DailyCallCounter(tmp_path / "calls", ceiling=100)
    gate = create_twelve_data_gate(clock=clock.time, sleep=clock.sleep, daily=counter)
    stamps = []

    def answer():
        stamps.append(clock.now)
        if len(stamps) == 1:
            raise Throttled(retry_after=1.0)
        return "bars"

    assert gate.call(answer, _throttle_hint) == "bars"
    assert counter.used() == 2


def test_the_twelve_data_gate_takes_its_ceiling_and_file_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TWELVE_DATA_DAILY_CEILING", "5")
    monkeypatch.setenv("TWELVE_DATA_USAGE_FILE", str(tmp_path / "td"))
    daily = create_twelve_data_gate().daily
    assert (daily.ceiling, daily.path) == (5, tmp_path / "td")

    monkeypatch.delenv("TWELVE_DATA_DAILY_CEILING")
    monkeypatch.delenv("TWELVE_DATA_USAGE_FILE")
    daily = create_twelve_data_gate().daily
    assert daily.ceiling == 780                       # 20 of the 800 left as headroom
    assert daily.path == Path.home() / ".local" / "state" / "trading-desk" / "twelve_data_calls"
    assert create_yahoo_gate().daily is None
