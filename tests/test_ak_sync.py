"""盘后 AkShare actual 行情同步的确定性测试，不访问外部网络。"""

from datetime import date, datetime
import plistlib
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data.ak_sync import (
    eligible_symbols,
    fetch_daily_histories,
    normalize_hist_frame,
    require_market_closed,
)


def history_frame(code="600001"):
    return pd.DataFrame(
        {
            "日期": ["2026-10-26"],
            "股票代码": [code],
            "开盘": [10.0],
            "收盘": [10.5],
            "最高": [10.8],
            "最低": [9.8],
            "成交量": [200],
            "成交额": [210000],
        }
    )


def test_normalize_hist_converts_hands_to_shares_and_maps_actual_fields():
    frame = normalize_hist_frame(history_frame(), expected_code="600001")

    row = frame.iloc[0]
    assert row["code"] == "600001"
    assert row["trade_date"] == date(2026, 10, 26)
    assert row["actual_open"] == 10.0
    assert row["actual_high"] == 10.8
    assert row["actual_low"] == 9.8
    assert row["actual_close"] == 10.5
    assert row["volume"] == 20_000
    assert row["amount"] == 210_000
    assert row["source_price_basis"] == "actual"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda frame: frame.assign(**{"股票代码": ["000002"]}), "证券代码不匹配"),
        (lambda frame: frame.assign(**{"成交量": [-1]}), "成交量"),
        (lambda frame: frame.assign(**{"成交额": [1]}), "量额与价格不匹配"),
        (lambda frame: frame.assign(**{"最高": [9.0]}), "OHLC"),
    ],
)
def test_normalize_rejects_bad_codes_units_and_ohlc(mutate, message):
    with pytest.raises(ValueError, match=message):
        normalize_hist_frame(mutate(history_frame()), expected_code="600001")


def test_normalize_rejects_duplicate_dates():
    frame = pd.concat([history_frame(), history_frame()], ignore_index=True)

    with pytest.raises(ValueError, match="重复交易日"):
        normalize_hist_frame(frame, expected_code="600001")


def test_eligible_symbols_only_includes_project_supported_a_share_prefixes():
    spot = pd.DataFrame(
        {"代码": ["600001", "000001", "300001", "688001", "920001", "900901"]}
    )

    assert eligible_symbols(spot) == ("000001", "300001", "600001")


def test_fetch_uses_actual_daily_history_and_retries_then_collects_failure():
    class FakeAkshare:
        def __init__(self):
            self.calls = []
            self.failures = 0

        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["600001", "000001"]})

        def stock_zh_a_hist(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["symbol"] == "000001":
                self.failures += 1
                raise TimeoutError("offline fixture")
            return history_frame(kwargs["symbol"])

    client = FakeAkshare()
    sleeps = []
    fetched = fetch_daily_histories(
        client,
        latest_dates={"600001": date(2026, 10, 23)},
        today=date(2026, 10, 27),
        new_symbol_lookback_days=90,
        retry_count=2,
        request_delay=0,
        sleep=sleeps.append,
    )

    assert fetched.symbols == ("000001", "600001")
    assert fetched.failed_symbols == ("000001",)
    assert len(fetched.normalized) == 1
    assert client.failures == 2
    assert client.calls[0]["period"] == "daily"
    assert client.calls[0]["adjust"] == ""
    known_symbol_request = next(
        request for request in client.calls if request["symbol"] == "600001"
    )
    assert known_symbol_request["start_date"] == "20261023"
    assert known_symbol_request["end_date"] == "20261027"
    assert sleeps == [1.0]


def test_new_symbol_fetch_uses_bounded_recent_window():
    class FakeAkshare:
        def __init__(self):
            self.request = None

        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["300001"]})

        def stock_zh_a_hist(self, **kwargs):
            self.request = kwargs
            return history_frame("300001")

    client = FakeAkshare()
    fetch_daily_histories(
        client,
        latest_dates={},
        today=date(2026, 10, 27),
        new_symbol_lookback_days=90,
        retry_count=1,
        request_delay=0,
        sleep=lambda _: None,
    )

    assert client.request["start_date"] == "20260729"


def test_fetch_stops_after_consecutive_provider_failures():
    class OfflineAkshare:
        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["600001", "600002", "600003"]})

        def stock_zh_a_hist(self, **_):
            raise TimeoutError("provider unavailable")

    with pytest.raises(ConnectionError, match="连续 2 个证券请求失败"):
        fetch_daily_histories(
            OfflineAkshare(),
            latest_dates={},
            today=date(2026, 10, 27),
            retry_count=1,
            request_delay=0,
            max_consecutive_failures=2,
            sleep=lambda _: None,
        )


def test_market_sync_is_blocked_until_after_close_in_shanghai_time():
    before_close = datetime(2026, 10, 27, 16, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    after_close = datetime(2026, 10, 27, 17, 1, tzinfo=ZoneInfo("Asia/Shanghai"))

    with pytest.raises(ValueError, match="收盘后"):
        require_market_closed(before_close)
    assert require_market_closed(after_close) == date(2026, 10, 27)


def test_launchd_template_runs_weekdays_after_market_close():
    path = Path(__file__).resolve().parents[1] / "scripts/launchd/com.stocktool.akshare-daily.plist"
    config = plistlib.loads(path.read_bytes())

    schedules = config["StartCalendarInterval"]
    assert [item["Weekday"] for item in schedules] == [1, 2, 3, 4, 5]
    assert all(item["Hour"] == 17 and item["Minute"] == 30 for item in schedules)



@pytest.mark.parametrize("invalid_date", [None, "NaT"])
def test_normalize_rejects_missing_trade_dates(invalid_date):
    frame = history_frame().assign(**{"日期": [invalid_date]})
    with pytest.raises(ValueError, match="日期"):
        normalize_hist_frame(frame, expected_code="600001")


def test_symbol_list_retries_transient_failure():
    class FlakyClient:
        attempts = 0

        def stock_zh_a_spot_em(self):
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("offline fixture")
            return pd.DataFrame({"代码": ["600001"]})

        def stock_zh_a_hist(self, **kwargs):
            return history_frame(kwargs["symbol"])

    client = FlakyClient()
    waits = []
    fetched = fetch_daily_histories(
        client, latest_dates={}, today=date(2026, 10, 26),
        retry_count=2, request_delay=0, sleep=waits.append,
    )
    assert client.attempts == 2
    assert waits == [1.0]
    assert fetched.stale_symbols == ()


def test_old_response_is_explicitly_marked_stale_not_latest():
    class OldClient:
        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["600001"]})

        def stock_zh_a_hist(self, **kwargs):
            return history_frame(kwargs["symbol"])

    fetched = fetch_daily_histories(
        OldClient(), latest_dates={"600001": date(2026, 10, 23)},
        today=date(2026, 10, 27), retry_count=1, request_delay=0,
    )
    assert fetched.stale_symbols == ("600001",)
    assert any("未确认最新交易日" in issue for issue in fetched.issues)
    assert len(fetched.normalized) == 1  # 保留可验证的补漏行，但不能声称最新。


def test_response_outside_requested_dates_is_rejected():
    class FutureClient:
        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["600001"]})

        def stock_zh_a_hist(self, **kwargs):
            return history_frame(kwargs["symbol"]).assign(**{"日期": ["2026-10-28"]})

    fetched = fetch_daily_histories(
        FutureClient(), latest_dates={}, today=date(2026, 10, 27),
        retry_count=1, request_delay=0,
    )
    assert fetched.failed_symbols == ("600001",)
    assert fetched.normalized.empty


def test_no_new_rows_with_stale_response_is_partial_and_keeps_parent(tmp_path):
    from data.ak_sync import FetchedHistories, _persist_incremental

    fetched = FetchedHistories(
        symbols=("600001",),
        normalized=normalize_hist_frame(history_frame(), expected_code="600001"),
        raw_frames=(history_frame(),), failed_symbols=(), empty_symbols=(),
        issues=("未确认最新交易日",), stale_symbols=("600001",),
    )
    report = _persist_incremental(
        None, fetched=fetched, parent_snapshot_id=17,
        latest_dates={"600001": date(2026, 10, 26)}, source_version="synthetic",
        fetched_at=datetime(2026, 10, 27, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
        raw_root=tmp_path,
    )
    assert report.status == "partial"
    assert report.price_snapshot_id == 17
    assert report.counts["symbols_stale"] == 1
    assert report.counts["rows_inserted"] == 0
    assert not list(tmp_path.iterdir())


def test_cli_returns_nonzero_for_partial_result(monkeypatch, capsys):
    from data import ak_sync
    from data.sync import SyncReport

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, *_):
            pass

        def fetchone(self):
            return (True,)

    class Connection(Cursor):
        def cursor(self):
            return Cursor()

    monkeypatch.setenv("DATABASE_URI", "postgresql://offline-fixture")
    monkeypatch.setattr(ak_sync.psycopg, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(
        ak_sync, "run", lambda *a, **k: SyncReport({}, 17, {"rows_inserted": 0}, (), "partial")
    )
    assert ak_sync.main([]) == 1
    assert '"status": "partial"' in capsys.readouterr().out
