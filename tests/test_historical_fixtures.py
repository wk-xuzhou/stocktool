"""P04：历史状态和板块变更夹具的结构验收。"""

from pathlib import Path

import pandas as pd


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "history"


def test_status_fixture_covers_st_suspension_and_delisting_transitions():
    frame = pd.read_csv(
        FIXTURE_DIR / "stock_status_daily.csv", dtype={"code": "string"}
    )

    assert list(frame.columns) == [
        "code",
        "trade_date",
        "is_st",
        "is_suspended",
        "is_delisted",
        "name",
        "source",
    ]
    assert frame["code"].astype(str).str.fullmatch(r"\d{6}").all()
    assert frame.groupby("code")["trade_date"].apply(
        lambda dates: dates.is_monotonic_increasing
    ).all()
    assert frame["is_st"].any()
    assert frame["is_suspended"].any()
    assert frame["is_delisted"].any()


def test_sector_membership_fixture_keeps_date_effective_membership():
    frame = pd.read_csv(
        FIXTURE_DIR / "sector_membership_daily.csv", dtype={"code": "string"}
    )

    assert list(frame.columns) == [
        "sector_code",
        "code",
        "trade_date",
        "source",
    ]
    assert frame["code"].astype(str).str.fullmatch(r"\d{6}").all()
    assert set(frame.loc[frame["trade_date"] == "2026-10-21", "code"]) == {
        "600001",
        "000001",
    }
    assert set(frame.loc[frame["trade_date"] == "2026-10-22", "code"]) == {
        "000001",
    }
