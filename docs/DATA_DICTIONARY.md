# DATA_DICTIONARY

**项目**：stocktool
**版本**：v1.0 / 2026-10-23

## 1. 单位与约定

- 成交量：**股**（通达信"手"×100 换算）；成交额：**元**；价格：`NUMERIC(12,3)`；
- 日期：`DATE`；时间戳：`TIMESTAMPTZ`；
- 复权：全库 `adj_type='qfq'`；代码统一 6 位字符串（北交所/科创板不入库）。

## 2. 表结构

### stock_basic

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) PK | 6位代码 |
| name | VARCHAR(32) | 名称 |
| market | VARCHAR(8) | SH/SZ/CYB |
| list_date | DATE | 上市日 |
| is_st | BOOLEAN | ST 标记（每日更新） |
| industry | VARCHAR(64) | 东财行业 |
| concepts | TEXT[] | 东财概念列表 |
| updated_at | TIMESTAMPTZ | |

### daily_price（按年分区）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) | |
| trade_date | DATE | 分区键 |
| open/high/low/close | NUMERIC(12,3) | 前复权 |
| volume | BIGINT | 股 |
| amount | NUMERIC(18,2) | 元 |
| adj_type | VARCHAR(8) | 默认 'qfq' |
| source | VARCHAR(16) | tdx/akshare |
| updated_at | TIMESTAMPTZ | |
| **PK** | (code, trade_date) | 含分区键 |

### sector_daily

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sector_code | VARCHAR(32) | 东财板块代码 |
| sector_name | VARCHAR(64) | |
| trade_date | DATE | |
| close | NUMERIC(12,3) | 板块指数 |
| pct_chg | NUMERIC(8,3) | 涨跌幅% |
| limit_up_count | INT | 板块内涨停家数（自算+校验） |
| **PK** | (sector_code, trade_date) | 数据量小，不分区 |

### stock_extra（按年分区）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) | |
| trade_date | DATE | 分区键 |
| market_cap | NUMERIC(18,2) | 总市值(元) |
| float_cap | NUMERIC(18,2) | 流通市值 |
| turnover_rate | NUMERIC(8,3) | 换手率% |
| is_suspended | BOOLEAN | 停牌标记 |
| **PK** | (code, trade_date) | |

### signals

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGSERIAL PK | |
| code / name | | |
| trigger_date | DATE | 信号日 T |
| buy_low / buy_high | NUMERIC(12,3) | 建议区间 |
| stop_loss | NUMERIC(12,3) | |
| score | SMALLINT | 0—100 |
| sub_scores | JSONB | 五因子分 |
| suggest_pos | NUMERIC(5,2) | 建议仓位% |
| reason | TEXT | 触发原因 |
| sector | VARCHAR(64) | 所属主线板块 |
| market_state | VARCHAR(8) | bull/neutral/bear |
| created_at | TIMESTAMPTZ | |

### backtest_runs

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGSERIAL PK | |
| run_at | TIMESTAMPTZ | |
| params | JSONB | 参数快照 |
| metrics | JSONB | 胜率/盈亏比/期望/回撤等 |
| trade_log | JSONB | 逐笔 |
| sample | VARCHAR(8) | in/out |

## 3. 分区 DDL（核心）

```sql
CREATE TABLE daily_price (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  open NUMERIC(12,3), high NUMERIC(12,3),
  low NUMERIC(12,3), close NUMERIC(12,3),
  volume BIGINT, amount NUMERIC(18,2),
  adj_type VARCHAR(8) DEFAULT 'qfq',
  source VARCHAR(16),
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

DO $$
DECLARE y INT;
BEGIN
  FOR y IN 2019..2026 LOOP
    EXECUTE format(
      'CREATE TABLE daily_price_%s PARTITION OF daily_price FOR VALUES FROM (%L) TO (%L);',
      y, y||'-01-01', (y+1)||'-01-01');
  END LOOP;
END $$;
CREATE TABLE daily_price_default PARTITION OF daily_price DEFAULT;

stock_extra 同法建分区；sector_daily、signals、backtest_runs 不分区。
