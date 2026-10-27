#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

: "${POSTGRES_HOST:=127.0.0.1}"
: "${POSTGRES_PORT:=15433}"
: "${POSTGRES_DB:=stocktool}"
: "${POSTGRES_USER:=stocktool}"
: "${POSTGRES_PASSWORD:=change-me}"

if command -v psql >/dev/null 2>&1 && command -v pg_isready >/dev/null 2>&1; then
  DB_CLIENT_MODE=host
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DB_CLIENT_MODE=container
else
  echo "错误：未找到 PostgreSQL 客户端，也未找到可用的 Docker Compose。" >&2
  exit 1
fi

run_pg_isready() {
  if [[ "$DB_CLIENT_MODE" == "container" ]]; then
    docker compose exec -T postgres pg_isready \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" >/dev/null
  else
    pg_isready \
      -h "$POSTGRES_HOST" \
      -p "$POSTGRES_PORT" \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" >/dev/null
  fi
}

run_psql() {
  if [[ "$DB_CLIENT_MODE" == "container" ]]; then
    docker compose exec -T \
      -e "PGPASSWORD=$POSTGRES_PASSWORD" \
      postgres psql "$@"
  else
    PGPASSWORD="$POSTGRES_PASSWORD" psql \
      --host "$POSTGRES_HOST" \
      --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" \
      --dbname "$POSTGRES_DB" \
      "$@"
  fi
}

if ! run_pg_isready; then
  echo "错误：无法连接 PostgreSQL ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}。" >&2
  echo "请先执行：docker compose up -d postgres" >&2
  exit 1
fi

run_psql --set ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS data_manifests (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source VARCHAR(32) NOT NULL,
  source_version VARCHAR(128) NOT NULL,
  price_basis VARCHAR(16) NOT NULL,
  start_date DATE,
  end_date DATE,
  code_count BIGINT NOT NULL DEFAULT 0,
  row_count BIGINT NOT NULL DEFAULT 0,
  source_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(16) NOT NULL DEFAULT 'building',
  notes TEXT
);

CREATE TABLE IF NOT EXISTS price_snapshots (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  adjustment_basis_date DATE,
  source_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
  input_actual_manifest_id BIGINT REFERENCES data_manifests(id),
  input_qfq_manifest_id BIGINT REFERENCES data_manifests(id),
  input_hfq_manifest_id BIGINT REFERENCES data_manifests(id),
  status VARCHAR(16) NOT NULL DEFAULT 'building',
  notes TEXT
);

ALTER TABLE data_manifests
  ADD COLUMN IF NOT EXISTS source_version VARCHAR(128),
  ADD COLUMN IF NOT EXISTS price_basis VARCHAR(16),
  ADD COLUMN IF NOT EXISTS code_count BIGINT NOT NULL DEFAULT 0;

ALTER TABLE price_snapshots
  ADD COLUMN IF NOT EXISTS input_actual_manifest_id BIGINT REFERENCES data_manifests(id),
  ADD COLUMN IF NOT EXISTS input_qfq_manifest_id BIGINT REFERENCES data_manifests(id),
  ADD COLUMN IF NOT EXISTS input_hfq_manifest_id BIGINT REFERENCES data_manifests(id);

CREATE OR REPLACE FUNCTION reject_referenced_manifest_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM price_snapshots
    WHERE OLD.id IN (
      input_actual_manifest_id,
      input_qfq_manifest_id,
      input_hfq_manifest_id
    )
  ) THEN
    RAISE EXCEPTION 'data_manifest % 已被价格快照引用，不能覆盖或删除', OLD.id;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS data_manifests_immutable_when_referenced ON data_manifests;
CREATE TRIGGER data_manifests_immutable_when_referenced
BEFORE UPDATE OR DELETE ON data_manifests
FOR EACH ROW EXECUTE FUNCTION reject_referenced_manifest_change();

CREATE TABLE IF NOT EXISTS stock_basic (
  code VARCHAR(10) PRIMARY KEY,
  name VARCHAR(32) NOT NULL,
  market VARCHAR(8) NOT NULL,
  list_date DATE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_price (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  price_snapshot_id BIGINT NOT NULL,
  qfq_open NUMERIC(12,3),
  qfq_high NUMERIC(12,3),
  qfq_low NUMERIC(12,3),
  qfq_close NUMERIC(12,3),
  hfq_open NUMERIC(12,3),
  hfq_high NUMERIC(12,3),
  hfq_low NUMERIC(12,3),
  hfq_close NUMERIC(12,3),
  actual_open NUMERIC(12,3),
  actual_high NUMERIC(12,3),
  actual_low NUMERIC(12,3),
  actual_close NUMERIC(12,3),
  adjustment_factor NUMERIC(20,10),
  source_price_basis VARCHAR(16) NOT NULL,
  volume BIGINT,
  amount NUMERIC(18,2),
  source VARCHAR(16) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (code, trade_date, price_snapshot_id)
) PARTITION BY RANGE (trade_date);

CREATE TABLE IF NOT EXISTS stock_status_daily (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  is_st BOOLEAN NOT NULL,
  is_suspended BOOLEAN NOT NULL,
  is_delisted BOOLEAN,
  name VARCHAR(32),
  source VARCHAR(16) NOT NULL,
  PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

ALTER TABLE stock_status_daily
  ADD COLUMN IF NOT EXISTS is_delisted BOOLEAN;

CREATE TABLE IF NOT EXISTS stock_extra (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  market_cap NUMERIC(18,2),
  float_cap NUMERIC(18,2),
  turnover_rate NUMERIC(8,3),
  source VARCHAR(16) NOT NULL,
  PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE TABLE IF NOT EXISTS sector_daily (
  sector_code VARCHAR(32) NOT NULL,
  sector_name VARCHAR(64) NOT NULL,
  trade_date DATE NOT NULL,
  close NUMERIC(12,3),
  pct_chg NUMERIC(8,3),
  limit_up_count INT,
  source VARCHAR(16) NOT NULL,
  PRIMARY KEY (sector_code, trade_date)
);

CREATE TABLE IF NOT EXISTS sector_membership_daily (
  sector_code VARCHAR(32) NOT NULL,
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  source VARCHAR(16) NOT NULL,
  PRIMARY KEY (sector_code, code, trade_date)
);

CREATE TABLE IF NOT EXISTS market_daily (
  trade_date DATE PRIMARY KEY,
  sh_close NUMERIC(12,3),
  sh_ma20 NUMERIC(12,3),
  limit_up_count INT,
  source VARCHAR(16) NOT NULL
);

DO $$
DECLARE
  y INT;
BEGIN
  FOR y IN 2019..2026 LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS daily_price_%s PARTITION OF daily_price FOR VALUES FROM (%L) TO (%L)',
      y, y || '-01-01', (y + 1) || '-01-01');
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS stock_status_daily_%s PARTITION OF stock_status_daily FOR VALUES FROM (%L) TO (%L)',
      y, y || '-01-01', (y + 1) || '-01-01');
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS stock_extra_%s PARTITION OF stock_extra FOR VALUES FROM (%L) TO (%L)',
      y, y || '-01-01', (y + 1) || '-01-01');
  END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS daily_price_default
  PARTITION OF daily_price DEFAULT;
CREATE TABLE IF NOT EXISTS stock_status_daily_default
  PARTITION OF stock_status_daily DEFAULT;
CREATE TABLE IF NOT EXISTS stock_extra_default
  PARTITION OF stock_extra DEFAULT;

CREATE TABLE IF NOT EXISTS signals (
  id BIGSERIAL PRIMARY KEY,
  code VARCHAR(10) NOT NULL,
  name VARCHAR(32) NOT NULL,
  trigger_date DATE NOT NULL,
  buy_low NUMERIC(12,3),
  buy_high NUMERIC(12,3),
  stop_loss NUMERIC(12,3),
  buy_low_actual NUMERIC(12,3),
  buy_high_actual NUMERIC(12,3),
  stop_loss_actual NUMERIC(12,3),
  score SMALLINT,
  sub_scores JSONB,
  suggest_pos NUMERIC(5,2),
  reason TEXT,
  sector VARCHAR(64),
  market_state VARCHAR(8),
  price_snapshot_id BIGINT NOT NULL,
  data_manifest_id BIGINT NOT NULL,
  params JSONB NOT NULL DEFAULT '{}'::jsonb,
  data_quality VARCHAR(16) NOT NULL DEFAULT 'partial',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id BIGSERIAL PRIMARY KEY,
  run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  params JSONB NOT NULL,
  price_snapshot_id BIGINT NOT NULL,
  data_manifest_id BIGINT NOT NULL,
  metrics JSONB,
  trade_log JSONB,
  sample VARCHAR(8),
  status VARCHAR(16) NOT NULL DEFAULT 'partial'
);
SQL

echo "数据库初始化完成：${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
