"""D03：数据库结构、分区和价格快照隔离的确定性验收测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT_DB = ROOT / "scripts" / "init_db.sh"


def schema_text() -> str:
    return INIT_DB.read_text()


def test_daily_price_is_versioned_and_contains_all_price_bases():
    sql = schema_text()

    assert "CREATE TABLE IF NOT EXISTS daily_price" in sql
    assert "price_snapshot_id BIGINT NOT NULL" in sql
    for column in (
        "qfq_open",
        "qfq_high",
        "qfq_low",
        "qfq_close",
        "actual_open",
        "actual_high",
        "actual_low",
        "actual_close",
        "hfq_open",
        "hfq_high",
        "hfq_low",
        "hfq_close",
        "source_price_basis",
    ):
        assert column in sql
    assert "PRIMARY KEY (code, trade_date, price_snapshot_id)" in sql
    assert "PARTITION BY RANGE (trade_date)" in sql


def test_required_price_and_input_version_tables_exist():
    sql = schema_text()

    for table in (
        "price_snapshots",
        "data_manifests",
        "stock_status_daily",
        "stock_extra",
        "sector_daily",
        "sector_membership_daily",
        "market_daily",
        "signals",
        "backtest_runs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "price_snapshot_id BIGINT" in sql
    assert "data_manifest_id BIGINT" in sql


def test_historical_status_preserves_delisting_state():
    sql = schema_text()

    assert "is_st BOOLEAN NOT NULL" in sql
    assert "is_suspended BOOLEAN NOT NULL" in sql
    assert "is_delisted BOOLEAN" in sql


def test_partitioned_tables_define_year_partitions_and_default_partitions():
    sql = schema_text()

    for table in ("daily_price", "stock_status_daily", "stock_extra"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"{table}_%s" in sql
        assert f"{table}_default" in sql

    assert "FOR y IN 2019..2026 LOOP" in sql
    assert "PARTITION OF daily_price DEFAULT" in sql
    assert "PARTITION OF stock_status_daily DEFAULT" in sql
    assert "PARTITION OF stock_extra DEFAULT" in sql


def test_daily_snapshots_store_only_deltas_and_expose_inherited_rows():
    sql = schema_text()

    assert "parent_price_snapshot_id BIGINT REFERENCES price_snapshots(id)" in sql
    assert "CREATE OR REPLACE FUNCTION daily_price_for_snapshot" in sql
    assert "WITH RECURSIVE snapshot_lineage" in sql
    assert "CREATE INDEX IF NOT EXISTS daily_price_snapshot_lookup" in sql


def test_init_db_is_idempotent_and_stops_on_sql_error():
    sql = schema_text()

    assert "set -euo pipefail" in sql
    assert "--set ON_ERROR_STOP=1" in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql


def test_version_tables_capture_basis_file_metadata_and_references():
    sql = schema_text()

    for fragment in (
        "source_version VARCHAR(128) NOT NULL",
        "price_basis VARCHAR(16) NOT NULL",
        "code_count BIGINT NOT NULL DEFAULT 0",
        "input_actual_manifest_id BIGINT REFERENCES data_manifests(id)",
        "input_qfq_manifest_id BIGINT REFERENCES data_manifests(id)",
        "input_hfq_manifest_id BIGINT REFERENCES data_manifests(id)",
        "reject_referenced_manifest_change",
    ):
        assert fragment in sql


def test_init_db_supports_container_postgresql_without_host_client():
    sql = schema_text()

    assert "docker compose exec -T postgres pg_isready" in sql
    assert "docker compose exec -T \\" in sql
    assert "postgres psql \"$@\"" in sql
    assert "command -v docker" in sql
