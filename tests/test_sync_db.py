"""D08：显式启用的本地 PostgreSQL 集成测试，测试写入全部回滚。"""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import conninfo_to_dict

from data import sync
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
                "FROM daily_price WHERE price_snapshot_id = %s", (snapshot_id,)
            )
            assert cursor.fetchone() == (row_count, 0, 0)
            cursor.execute(
                "SELECT actual_close FROM daily_price "
                "WHERE price_snapshot_id = %s AND trade_date = '2026-10-23'",
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
