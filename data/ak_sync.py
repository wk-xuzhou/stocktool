"""盘后从 AkShare 获取 A 股 actual 日线并增量写入版本快照。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from time import sleep
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
from dotenv import dotenv_values, load_dotenv

from data import sync
from data.sync import SyncReport
from data.versioning import (
    PriceSnapshot,
    SourceInput,
    build_data_manifest,
    persist_data_manifest,
    persist_price_snapshot,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SUPPORTED_CODE_PREFIXES = ("00", "30", "60")
_HISTORY_COLUMNS = (
    "日期",
    "股票代码",
    "开盘",
    "收盘",
    "最高",
    "最低",
    "成交量",
    "成交额",
)


@dataclass(frozen=True)
class FetchedHistories:
    symbols: tuple[str, ...]
    normalized: pd.DataFrame
    raw_frames: tuple[pd.DataFrame, ...]
    failed_symbols: tuple[str, ...]
    empty_symbols: tuple[str, ...]
    issues: tuple[str, ...]
    stale_symbols: tuple[str, ...] = ()


def require_market_closed(now: datetime) -> date:
    """只允许北京时间 17:00 后运行，避开未完成的当日 K 线。"""

    if now.tzinfo is None:
        raise ValueError("运行时间必须包含时区")
    shanghai_now = now.astimezone(_SHANGHAI)
    if shanghai_now.time() < time(17, 0):
        raise ValueError("自动行情同步仅允许北京时间 17:00 收盘后运行")
    return shanghai_now.date()


def eligible_symbols(spot_frame: pd.DataFrame) -> tuple[str, ...]:
    """从实时证券列表中选取项目范围内的沪深主板和创业板代码。"""

    if "代码" not in spot_frame.columns:
        raise ValueError("AkShare 实时证券列表缺少代码列")
    codes = spot_frame["代码"].astype("string").str.strip().str.zfill(6)
    if codes.isna().any() or not codes.str.fullmatch(r"\d{6}").all():
        raise ValueError("AkShare 实时证券列表含无效证券代码")
    symbols = sorted(
        {
            code
            for code in codes
            if code.startswith(_SUPPORTED_CODE_PREFIXES)
        }
    )
    if not symbols:
        raise ValueError("AkShare 实时证券列表中没有项目范围内的 A 股代码")
    return tuple(symbols)


def normalize_hist_frame(
    source_frame: pd.DataFrame, *, expected_code: str
) -> pd.DataFrame:
    """校验 AkShare 日线并转换为项目实际价和股/元单位。"""

    missing = set(_HISTORY_COLUMNS).difference(source_frame.columns)
    if missing:
        raise ValueError(f"AkShare 日线缺少字段：{', '.join(sorted(missing))}")
    if source_frame.empty:
        return _empty_normalized_frame()

    source_codes = source_frame["股票代码"].astype("string").str.strip().str.zfill(6)
    if source_codes.isna().any() or not source_codes.eq(expected_code).all():
        raise ValueError(f"AkShare 返回证券代码不匹配：要求 {expected_code}")

    try:
        dates = pd.to_datetime(source_frame["日期"], errors="raise").dt.date
        numeric = {
            column: pd.to_numeric(source_frame[column], errors="raise")
            for column in ("开盘", "收盘", "最高", "最低", "成交量", "成交额")
        }
    except (TypeError, ValueError) as exc:
        raise ValueError(f"AkShare 日线含无效日期或数值：{expected_code}") from exc

    if dates.isna().any():
        raise ValueError(f"AkShare 日线含缺失日期：{expected_code}")
    if dates.duplicated().any():
        raise ValueError(f"AkShare 日线存在重复交易日：{expected_code}")
    open_price = numeric["开盘"].astype(float)
    close_price = numeric["收盘"].astype(float)
    high_price = numeric["最高"].astype(float)
    low_price = numeric["最低"].astype(float)
    volume_hands = numeric["成交量"].astype(float)
    amount = numeric["成交额"].astype(float)
    values = pd.concat(
        [open_price, close_price, high_price, low_price, volume_hands, amount],
        axis=1,
    )
    if not values.map(lambda value: pd.notna(value) and abs(value) != float("inf")).all().all():
        raise ValueError(f"AkShare 日线含空值或非有限数：{expected_code}")
    if (low_price <= 0).any():
        raise ValueError(f"AkShare 日线价格为负/零：{expected_code}")
    if (volume_hands < 0).any():
        raise ValueError(f"AkShare 成交量为负：{expected_code}")
    if (amount < 0).any():
        raise ValueError(f"AkShare 成交额为负：{expected_code}")
    if (volume_hands % 1 != 0).any():
        raise ValueError(f"AkShare 成交量不是整数手：{expected_code}")
    if (
        (high_price < pd.concat([open_price, close_price, low_price], axis=1).max(axis=1))
        | (low_price > pd.concat([open_price, close_price, high_price], axis=1).min(axis=1))
    ).any():
        raise ValueError(f"AkShare 日线 OHLC 关系非法：{expected_code}")

    volume_shares = volume_hands * 100
    traded = volume_shares > 0
    average_price = amount[traded] / volume_shares[traded]
    tolerance = pd.concat([low_price[traded], high_price[traded]], axis=1).max(axis=1) * 0.01
    if (
        (average_price < low_price[traded] - tolerance)
        | (average_price > high_price[traded] + tolerance)
    ).any():
        raise ValueError(f"AkShare 量额与价格不匹配，无法确认成交量为手：{expected_code}")
    if ((~traded) & (amount > 0)).any():
        raise ValueError(f"AkShare 零成交量但成交额非零：{expected_code}")

    normalized = pd.DataFrame(
        {
            "code": expected_code,
            "trade_date": dates,
            "actual_open": open_price,
            "actual_high": high_price,
            "actual_low": low_price,
            "actual_close": close_price,
            "qfq_open": float("nan"),
            "qfq_high": float("nan"),
            "qfq_low": float("nan"),
            "qfq_close": float("nan"),
            "hfq_open": float("nan"),
            "hfq_high": float("nan"),
            "hfq_low": float("nan"),
            "hfq_close": float("nan"),
            "adjustment_factor": float("nan"),
            "source_price_basis": "actual",
            "volume": volume_shares.astype("int64"),
            "amount": amount,
            "source": "akshare",
        }
    )
    return normalized.sort_values("trade_date").reset_index(drop=True)


def _empty_normalized_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=(
            "code",
            "trade_date",
            "actual_open",
            "actual_high",
            "actual_low",
            "actual_close",
            "qfq_open",
            "qfq_high",
            "qfq_low",
            "qfq_close",
            "hfq_open",
            "hfq_high",
            "hfq_low",
            "hfq_close",
            "adjustment_factor",
            "source_price_basis",
            "volume",
            "amount",
            "source",
        )
    )


def fetch_daily_histories(
    client,
    *,
    latest_dates: dict[str, date],
    today: date,
    new_symbol_lookback_days: int = 90,
    retry_count: int = 3,
    request_delay: float = 0.2,
    max_consecutive_failures: int = 10,
    sleep: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> FetchedHistories:
    """逐证券获取不复权日线；单证券失败重试并记录，不伪造缺失行情。"""

    if (
        retry_count < 1
        or request_delay < 0
        or new_symbol_lookback_days < 1
        or max_consecutive_failures < 1
    ):
        raise ValueError("重试次数、请求间隔和新证券回看天数参数无效")
    for attempt in range(retry_count):
        try:
            symbols = eligible_symbols(client.stock_zh_a_spot_em())
            break
        except Exception:
            if attempt + 1 == retry_count:
                raise
            sleep(float(2**attempt))
    normalized_frames: list[pd.DataFrame] = []
    raw_frames: list[pd.DataFrame] = []
    failed_symbols: list[str] = []
    empty_symbols: list[str] = []
    issues: list[str] = []
    stale_symbols: list[str] = []
    consecutive_failures = 0

    for index, symbol in enumerate(symbols):
        start_date = latest_dates.get(symbol) or (
            today - timedelta(days=new_symbol_lookback_days)
        )
        request = {
            "symbol": symbol,
            "period": "daily",
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": today.strftime("%Y%m%d"),
            "adjust": "",
            "timeout": 20,
        }
        for attempt in range(retry_count):
            try:
                response = client.stock_zh_a_hist(**request)
                if not isinstance(response, pd.DataFrame):
                    raise ValueError("AkShare 日线返回值不是 DataFrame")
                if response.empty:
                    consecutive_failures = 0
                    empty_symbols.append(symbol)
                    issues.append(f"AkShare 无日线返回：{symbol}")
                    break
                normalized = normalize_hist_frame(response, expected_code=symbol)
                if (
                    (normalized["trade_date"] < start_date).any()
                    or (normalized["trade_date"] > today).any()
                ):
                    raise ValueError(f"AkShare 返回日期超出请求范围：{symbol}")
                normalized_frames.append(normalized)
                # 没有可信交易日历/停牌资料时，旧日线只能视为新鲜度未确认。
                # 周末按周五检查；节假日和停牌不伪造补齐，明确报告 partial。
                expected_date = today - timedelta(days=max(today.weekday() - 4, 0))
                if normalized["trade_date"].max() < expected_date:
                    stale_symbols.append(symbol)
                    issues.append(
                        f"AkShare 未确认最新交易日：{symbol}，"
                        f"实际末日 {normalized['trade_date'].max()}，"
                        f"待确认 {expected_date}（含节假日/停牌可能）"
                    )
                consecutive_failures = 0
                raw = response.copy()
                raw["同步请求代码"] = symbol
                raw_frames.append(raw)
                break
            except Exception as exc:
                if attempt + 1 == retry_count:
                    failed_symbols.append(symbol)
                    consecutive_failures += 1
                    issues.append(f"AkShare 请求失败：{symbol}：{type(exc).__name__}: {exc}")
                else:
                    sleep(float(2**attempt))
        if index + 1 < len(symbols) and request_delay:
            sleep(request_delay)
        if progress and (index + 1) % 100 == 0:
            progress(f"已请求 {index + 1}/{len(symbols)} 个证券")
        if consecutive_failures >= max_consecutive_failures:
            raise ConnectionError(
                f"AkShare 连续 {consecutive_failures} 个证券请求失败，停止本批次"
            )

    normalized = (
        pd.concat(normalized_frames, ignore_index=True)
        if normalized_frames
        else _empty_normalized_frame()
    )
    if not normalized.empty and normalized.duplicated(["code", "trade_date"]).any():
        raise ValueError("AkShare 多证券日线存在重复代码/交易日")
    return FetchedHistories(
        symbols=symbols,
        normalized=normalized,
        raw_frames=tuple(raw_frames),
        failed_symbols=tuple(failed_symbols),
        empty_symbols=tuple(empty_symbols),
        issues=tuple(issues),
        stale_symbols=tuple(stale_symbols),
    )


def _persist_incremental(
    conn,
    *,
    fetched: FetchedHistories,
    parent_snapshot_id: int,
    latest_dates: dict[str, date],
    source_version: str,
    fetched_at: datetime,
    raw_root: Path,
) -> SyncReport:
    frame = fetched.normalized
    new_frame = sync._filter_new_dates(frame, latest_dates)
    status = (
        "partial"
        if fetched.failed_symbols or fetched.empty_symbols or fetched.stale_symbols
        else "complete"
    )
    if new_frame.empty:
        return SyncReport(
            data_manifest_ids={},
            price_snapshot_id=parent_snapshot_id,
            counts={
                "files_total": 1,
                "files_imported": 0,
                "files_empty": len(fetched.empty_symbols),
                "files_invalid": len(fetched.failed_symbols),
                "rows_seen": len(frame),
                "rows_inserted": 0,
                "rows_skipped": len(frame),
                "symbols_requested": len(fetched.symbols),
                "symbols_failed": len(fetched.failed_symbols),
                "symbols_empty": len(fetched.empty_symbols),
                "symbols_stale": len(fetched.stale_symbols),
            },
            issues=fetched.issues,
            status=status,
        )

    raw_root.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = raw_root / f"akshare_daily_{stamp}.csv"
    raw_frame = pd.concat(fetched.raw_frames, ignore_index=True)
    temporary_path = raw_path.with_suffix(".csv.tmp")
    raw_frame.to_csv(temporary_path, index=False, encoding="utf-8")
    temporary_path.replace(raw_path)

    manifest = build_data_manifest(
        SourceInput(
            path=raw_path,
            frame=frame,
            source="akshare",
            source_version=source_version,
            price_basis="actual",
        ),
        status=status,
    )
    manifest = replace(manifest, status=status)
    manifest_id = persist_data_manifest(conn, manifest)
    source_manifest = {
        "inputs": {"actual": sync._input_metadata(manifest, manifest_id)},
        "parent_price_snapshot_id": parent_snapshot_id,
        "provider": {
            "api": "stock_zh_a_hist(adjust='')",
            "fetched_at": fetched_at.isoformat(),
            "symbols_requested": len(fetched.symbols),
            "symbols_failed": list(fetched.failed_symbols),
            "symbols_empty": list(fetched.empty_symbols),
            "symbols_stale": list(fetched.stale_symbols),
        },
    }
    snapshot_id = persist_price_snapshot(
        conn,
        PriceSnapshot(
            id=0,
            input_manifest_ids={"actual": manifest_id},
            source_manifest=source_manifest,
            status=status,
            parent_snapshot_id=parent_snapshot_id,
        ),
    )
    inserted = sync.import_price_frame(
        conn, new_frame, price_snapshot_id=snapshot_id, source="akshare"
    )
    return SyncReport(
        data_manifest_ids={"actual": manifest_id},
        price_snapshot_id=snapshot_id,
        counts={
            "files_total": 1,
            "files_imported": 1,
            "files_empty": len(fetched.empty_symbols),
            "files_invalid": len(fetched.failed_symbols),
            "rows_seen": len(frame),
            "rows_inserted": inserted,
            "rows_skipped": len(frame) - inserted,
            "symbols_requested": len(fetched.symbols),
            "symbols_failed": len(fetched.failed_symbols),
            "symbols_empty": len(fetched.empty_symbols),
            "symbols_stale": len(fetched.stale_symbols),
        },
        issues=fetched.issues,
        status=status,
    )


def run(
    conn,
    *,
    client=None,
    now: datetime | None = None,
    request_delay: float = 0.2,
    retry_count: int = 3,
    max_consecutive_failures: int = 10,
    new_symbol_lookback_days: int = 90,
    raw_root: str | Path = "data/raw/akshare_daily",
    progress: Callable[[str], None] | None = None,
) -> SyncReport:
    """在北京时间收盘后同步当前沪深主板和创业板 actual 日线。"""

    fetched_at = now or datetime.now(_SHANGHAI)
    today = require_market_closed(fetched_at)
    parent_snapshot_id, latest_dates = sync._latest_actual_snapshot(conn)
    if client is None:
        import akshare as client

    fetched = fetch_daily_histories(
        client,
        latest_dates=latest_dates,
        today=today,
        new_symbol_lookback_days=new_symbol_lookback_days,
        retry_count=retry_count,
        max_consecutive_failures=max_consecutive_failures,
        request_delay=request_delay,
        progress=progress,
    )
    try:
        provider_version = importlib.metadata.version("akshare")
    except importlib.metadata.PackageNotFoundError:
        provider_version = "unknown"
    source_version = f"akshare-{provider_version}-{today:%Y%m%d}-{fetched_at:%H%M%S}"
    return _persist_incremental(
        conn,
        fetched=fetched,
        parent_snapshot_id=parent_snapshot_id,
        latest_dates=latest_dates,
        source_version=source_version,
        fetched_at=fetched_at,
        raw_root=Path(raw_root),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="盘后同步 A 股 actual 日线")
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args(argv)
    if args.request_delay < 0 or args.retries < 1:
        parser.error("--request-delay 必须非负，--retries 必须大于 0")

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    uri = os.environ.get("DATABASE_URI") or dotenv_values(
        Path(__file__).resolve().parents[1] / ".env.example"
    ).get("DATABASE_URI")
    if not uri:
        parser.error("请在 .env 中配置 DATABASE_URI")
    with psycopg.connect(
        uri, application_name="stocktool_akshare_daily_sync", connect_timeout=10
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(7401120261027)")
            lock_acquired = cursor.fetchone()[0]
        if not lock_acquired:
            print("已有行情同步进程运行，本次不重复启动。")
            return 0
        report = run(
            conn,
            request_delay=args.request_delay,
            retry_count=args.retries,
            progress=lambda message: print(message, flush=True),
        )
    print(json.dumps(asdict(report), ensure_ascii=False, default=str, indent=2))
    if report.status != "complete":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
