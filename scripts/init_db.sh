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

command -v psql >/dev/null 2>&1 || {
  echo "错误：未找到 psql，请先安装 PostgreSQL 客户端。" >&2
  exit 1
}
command -v pg_isready >/dev/null 2>&1 || {
  echo "错误：未找到 pg_isready，请先安装 PostgreSQL 客户端。" >&2
  exit 1
}

export PGPASSWORD="$POSTGRES_PASSWORD"
if ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null; then
  echo "错误：无法连接 PostgreSQL ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}。" >&2
  echo "请先执行：docker compose up -d postgres" >&2
  exit 1
fi

psql \
  --host "$POSTGRES_HOST" \
  --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS stock_basic (
  code VARCHAR(10) PRIMARY KEY,
  name VARCHAR(32) NOT NULL,
  market VARCHAR(8) NOT NULL,
  list_date DATE,
  is_st BOOLEAN NOT NULL DEFAULT FALSE,
  industry VARCHAR(64),
  concepts TEXT[] NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_price (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  open NUMERIC(12,3),
  high NUMERIC(12,3),
  low NUMERIC(12,3),
  close NUMERIC(12,3),
  volume BIGINT,
  amount NUMERIC(18,2),
  adj_type VARCHAR(8) NOT NULL DEFAULT 'qfq',
  source VARCHAR(16),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE TABLE IF NOT EXISTS stock_extra (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  market_cap NUMERIC(18,2),
  float_cap NUMERIC(18,2),
  turnover_rate NUMERIC(8,3),
  is_suspended BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

DO $$
DECLARE
  y INT;
BEGIN
  FOR y IN 2019..2026 LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS daily_price_%s PARTITION OF daily_price FOR VALUES FROM (%L) TO (%L)',
      y, y || '-01-01', (y + 1) || '-01-01');
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS stock_extra_%s PARTITION OF stock_extra FOR VALUES FROM (%L) TO (%L)',
      y, y || '-01-01', (y + 1) || '-01-01');
  END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS daily_price_default
  PARTITION OF daily_price DEFAULT;
CREATE TABLE IF NOT EXISTS stock_extra_default
  PARTITION OF stock_extra DEFAULT;

CREATE TABLE IF NOT EXISTS sector_daily (
  sector_code VARCHAR(32) NOT NULL,
  sector_name VARCHAR(64) NOT NULL,
  trade_date DATE NOT NULL,
  close NUMERIC(12,3),
  pct_chg NUMERIC(8,3),
  limit_up_count INT NOT NULL DEFAULT 0,
  PRIMARY KEY (sector_code, trade_date)
);

CREATE TABLE IF NOT EXISTS signals (
  id BIGSERIAL PRIMARY KEY,
  code VARCHAR(10) NOT NULL,
  name VARCHAR(32) NOT NULL,
  trigger_date DATE NOT NULL,
  buy_low NUMERIC(12,3),
  buy_high NUMERIC(12,3),
  stop_loss NUMERIC(12,3),
  score SMALLINT,
  sub_scores JSONB,
  suggest_pos NUMERIC(5,2),
  reason TEXT,
  sector VARCHAR(64),
  market_state VARCHAR(8),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id BIGSERIAL PRIMARY KEY,
  run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  params JSONB NOT NULL,
  metrics JSONB,
  trade_log JSONB,
  sample VARCHAR(8)
);
SQL

echo "数据库初始化完成：${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
