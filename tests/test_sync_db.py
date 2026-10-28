"""D08：显式启用的本地 PostgreSQL 集成测试，测试写入全部回滚。"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pandas as pd

from data import ak_sync, sync
from tests.test_sync import write_tdx_file


pytestmark = pytest.mark.skipif(
    os.environ.get("STOCKTOOL_RUN_DB_TESTS") != "1",
    reason="本地 PostgreSQL 集成测试须显式设置 STOCKTOOL_RUN_DB_TESTS=1",
)


@pytest.fixture
def db_conn():
    config = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    uri = os.environ.get("DATABASE_URI") or config.get("DATABASE_URI")
    if not uri:
        pytest.fail("本地数据库集成测试需要配置 DATABASE_URI")
    params = conninfo_to_dict(uri)
    assert params.get("host") in {"127.0.0.1", "localhost"}, "仅允许本地数据库"
    assert params.get("hostaddr", "127.0.0.1") == "127.0.0.1", "仅允许本地地址"
    assert params.get("port") == "15433", "仅允许本项目 15433 端口"
    assert params.get("dbname") == "stocktool", "仅允许 stocktool 数据库"
    conn = psycopg.connect(uri, connect_timeout=5)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone()[0] == "stocktool", "仅允许 stocktool 测试数据库"
        yield conn
    finally:
        conn.rollback()
        conn.close()


def version_counts(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM data_manifests), "
            "(SELECT COUNT(*) FROM price_snapshots), "
            "(SELECT COUNT(*) FROM daily_price)"
        )
        return cursor.fetchone()


def test_actual_history_idempotency_incremental_and_snapshot_isolation(db_conn, tmp_path):
    path = write_tdx_file(
        tmp_path / "SH#600001.txt", "600001 测试银行 日线 不复权",
        [
            "2026/10/22,10,11,9,10.5,100,1050",
            "2026/10/23,11,12,10,11.5,110,1265",
        ],
    )
    initial = version_counts(db_conn)
    full = sync.run(
        "full_actual", db_conn, input_dir=tmp_path, source_version="synthetic-history"
    )
    assert full.status == "complete"
    assert full.counts["rows_inserted"] == 2
    assert sync.import_tdx_file(
        db_conn, path, price_snapshot_id=full.price_snapshot_id
    ) == 0
    assert version_counts(db_conn) == tuple(
        value + delta for value, delta in zip(initial, (1, 1, 2), strict=True)
    )

    write_tdx_file(
        path, "600001 测试银行 日线 不复权",
        [
            "2026/10/22,10,11,9,10.5,100,1050",
            "2026/10/23,11,12,10,11.25,110,1265",
            "2026/10/26,12,13,11,12.5,120,1500",
        ],
    )
    delta = sync.run(
        "incremental", db_conn, input_dir=tmp_path, source_version="synthetic-delta"
    )
    assert delta.price_snapshot_id != full.price_snapshot_id
    assert delta.counts["rows_inserted"] == 1
    assert delta.counts["rows_skipped"] == 2
    with db_conn.cursor() as cursor:
        for snapshot_id, row_count in (
            (full.price_snapshot_id, 2), (delta.price_snapshot_id, 3)
        ):
            cursor.execute(
                "SELECT COUNT(*), COUNT(qfq_close), COUNT(hfq_close) "
                "FROM daily_price_for_snapshot(%s)", (snapshot_id,)
            )
            assert cursor.fetchone() == (row_count, 0, 0)
            cursor.execute(
                "SELECT actual_close FROM daily_price_for_snapshot(%s) "
                "WHERE trade_date = '2026-10-23'",
                (snapshot_id,),
            )
            assert float(cursor.fetchone()[0]) == 11.5
    before_noop = version_counts(db_conn)
    noop = sync.run(
        "incremental", db_conn, input_dir=tmp_path, source_version="synthetic-noop"
    )
    assert noop.price_snapshot_id == delta.price_snapshot_id
    assert noop.counts["rows_inserted"] == 0
    assert version_counts(db_conn) == before_noop


def test_akshare_incremental_stores_delta_and_resolves_parent_snapshot(
    db_conn, tmp_path
):
    with db_conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO data_manifests "
            "(source,source_version,price_basis,start_date,end_date,code_count,row_count,status) "
            "VALUES ('tdx','ak-test-parent','actual','2026-10-23','2026-10-23',1,1,'complete') "
            "RETURNING id"
        )
        manifest_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO price_snapshots "
            "(input_actual_manifest_id,status) VALUES (%s,'complete') RETURNING id",
            (manifest_id,),
        )
        parent_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO daily_price "
            "(code,trade_date,price_snapshot_id,actual_open,actual_high,actual_low,actual_close,"
            "source_price_basis,volume,amount,source) "
            "VALUES ('600001','2026-10-23',%s,10,11,9,10.5,'actual',10000,105000,'tdx')",
            (parent_id,),
        )
        cursor.execute("SAVEPOINT akshare_incremental_test")
    before = version_counts(db_conn)

    class FakeAkshare:
        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["600001", "000001", "688001"]})

        def stock_zh_a_hist(self, *, symbol, **_):
            rows = [["2026-10-26", symbol, 10.0, 10.5, 10.8, 9.8, 200, 210000]]
            if symbol == "600001":
                rows.insert(
                    0,
                    ["2026-10-23", symbol, 10.0, 10.5, 10.8, 9.8, 200, 210000],
                )
            return pd.DataFrame(
                rows,
                columns=["日期", "股票代码", "开盘", "收盘", "最高", "最低", "成交量", "成交额"],
            )

    report = ak_sync.run(
        db_conn,
        client=FakeAkshare(),
        now=datetime(2026, 10, 26, 18, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
        request_delay=0,
        raw_root=tmp_path,
    )
    assert report.status == "complete"
    assert report.counts["symbols_requested"] == 2
    assert report.counts["rows_inserted"] == 2
    assert db_conn.execute(
        "SELECT parent_price_snapshot_id FROM price_snapshots WHERE id=%s",
        (report.price_snapshot_id,),
    ).fetchone()[0] == parent_id
    assert db_conn.execute(
        "SELECT COUNT(*) FROM daily_price WHERE price_snapshot_id=%s",
        (report.price_snapshot_id,),
    ).fetchone()[0] == 2
    assert db_conn.execute(
        "SELECT COUNT(*) FROM daily_price_for_snapshot(%s)",
        (report.price_snapshot_id,),
    ).fetchone()[0] == 3
    assert db_conn.execute(
        "SELECT volume FROM daily_price_for_snapshot(%s) "
        "WHERE code='000001' AND trade_date='2026-10-26'",
        (report.price_snapshot_id,),
    ).fetchone()[0] == 20000

    noop = ak_sync.run(
        db_conn, client=FakeAkshare(),
        now=datetime(2026, 10, 26, 18, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
        request_delay=0, raw_root=tmp_path,
    )
    assert noop.price_snapshot_id == report.price_snapshot_id
    assert noop.counts["rows_inserted"] == 0

    class PartlyOfflineAkshare(FakeAkshare):
        def stock_zh_a_hist(self, *, symbol, **kwargs):
            if symbol == "000001":
                raise TimeoutError("offline fixture")
            return super().stock_zh_a_hist(symbol=symbol, **kwargs).assign(
                **{"日期": ["2026-10-26", "2026-10-27"]}
            )

    partial = ak_sync.run(
        db_conn, client=PartlyOfflineAkshare(),
        now=datetime(2026, 10, 27, 18, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
        retry_count=1, request_delay=0, raw_root=tmp_path,
    )
    assert partial.status == "partial"
    assert partial.counts["rows_inserted"] == 1
    assert partial.counts["symbols_failed"] == 1
    assert db_conn.execute(
        "SELECT parent_price_snapshot_id FROM price_snapshots WHERE id=%s",
        (partial.price_snapshot_id,),
    ).fetchone()[0] == report.price_snapshot_id
    assert db_conn.execute(
        "SELECT COUNT(*) FROM daily_price_for_snapshot(%s)",
        (partial.price_snapshot_id,),
    ).fetchone()[0] == 4
    assert db_conn.execute(
        "SELECT COUNT(*) FROM daily_price_for_snapshot(%s)",
        (report.price_snapshot_id,),
    ).fetchone()[0] == 3

    with db_conn.cursor() as cursor:
        cursor.execute("ROLLBACK TO SAVEPOINT akshare_incremental_test")
    assert version_counts(db_conn) == before


def test_changed_source_failure_rolls_back_manifest_snapshot_and_written_rows(
    db_conn, tmp_path, monkeypatch
):
    for code in ("600001", "600002"):
        write_tdx_file(
            tmp_path / f"SH#{code}.txt", f"{code} 测试银行 日线 不复权",
            ["2026/10/23,11,12,10,11.5,110,1265"],
        )
    initial = version_counts(db_conn)
    original_import = sync.import_price_frame
    inserted = []

    def change_next_source(conn, frame, **kwargs):
        inserted.append(original_import(conn, frame, **kwargs))
        path = tmp_path / "SH#600002.txt"
        path.write_bytes(path.read_bytes().replace(b"11.5", b"11.25"))
        return inserted[-1]

    monkeypatch.setattr(sync, "import_price_frame", change_next_source)
    with db_conn.cursor() as cursor:
        cursor.execute("SAVEPOINT source_changed_test")
    with pytest.raises(ValueError, match="文件.*变化"):
        sync.run(
            "full_actual", db_conn, input_dir=tmp_path,
            source_version="synthetic-rollback",
        )
    assert inserted == [1]
    with db_conn.cursor() as cursor:
        cursor.execute("ROLLBACK TO SAVEPOINT source_changed_test")
    assert version_counts(db_conn) == initial
