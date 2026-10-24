# DATA_DICTIONARY

**项目**：stocktool
**版本**：v1.1 / 2026-10-24

## 1. 单位与数据口径

- 成交量统一为股（通达信手数 ×100）；成交额为元；价格使用 `NUMERIC(12,3)`，市值/成交额使用元。
- 日期使用 `DATE`，采集和更新时间使用 `TIMESTAMPTZ`；代码统一为 6 位字符串。
- `daily_price` 只存前复权（qfq）价格；复权因子、原始价格和复权快照 ID 必须保留，以便审计和复现。
- 任何回测和信号均绑定不可变的 `price_snapshot_id`。全量复权生成新快照，不覆盖已有快照；旧快照只有在无信号或回测引用且显式清理时才可删除。
- 历史回测必须使用历史时点的 ST 状态、证券状态、行业/概念板块成分、市值和停牌信息。若某日期所需快照缺失，该日期标记数据不完整并从正式指标中排除，不能以前值或当前值静默填充。

## 2. 表结构

### stock_basic（当前证券主档）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) PK | 6 位证券代码 |
| name | VARCHAR(32) | 当前名称，仅用于展示 |
| market | VARCHAR(8) | SH/SZ/CYB |
| list_date | DATE | 上市日期 |
| updated_at | TIMESTAMPTZ | 更新时间 |

当前主档不得用来回填历史名称、ST、行业或板块归属。历史池筛选使用下列日期表。

### daily_price（按年分区、按快照版本保留）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) | 证券代码 |
| trade_date | DATE | 分区键 |
| price_snapshot_id | BIGINT | 复权快照 ID |
| open/high/low/close | NUMERIC(12,3) | qfq 价格 |
| hfq_open/hfq_high/hfq_low/hfq_close | NUMERIC(12,3) | 通达信后复权源价格；若源为其他口径须在清单中声明 |
| actual_open/actual_high/actual_low/actual_close | NUMERIC(12,3) | 未复权实际成交价格，用于涨跌停校验和成交模拟 |
| adjustment_factor | NUMERIC(20,10) | 源行情到 actual、actual 到 qfq 的换算因子及来源版本 |
| source_price_basis | VARCHAR(16) | hfq/actual/other |
| volume | BIGINT | 股 |
| amount | NUMERIC(18,2) | 元 |
| source | VARCHAR(16) | tdx/akshare |
| updated_at | TIMESTAMPTZ | 入库时间 |
| **PK** | (code, trade_date, price_snapshot_id) | 含分区键 |

### price_snapshots（不可变价格版本清单）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGSERIAL PK | 快照 ID |
| created_at | TIMESTAMPTZ | 生成时间 |
| adjustment_basis_date | DATE | qfq 基准日 |
| source_manifest | JSONB | 输入文件/数据源及校验和 |
| status | VARCHAR(16) | building/complete/invalid |
| notes | TEXT | 生成说明、校验结果摘要 |

后复权转前复权不得假定仅凭后复权 OHLCV 就能恢复。必须从通达信导出中取得可验证的换算因子，或补充可信的公司行为/复权因子数据；因子来源不可验证时拒绝生成正式价格快照。技术指标使用 qfq，涨跌停校验及模拟成交使用 actual 价格。AkShare 对比必须明确采用相同复权类型和同一基准日。

### stock_status_daily（历史证券状态，按年分区）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) | 证券代码 |
| trade_date | DATE | 分区键 |
| is_st | BOOLEAN | T 日 ST 状态 |
| is_suspended | BOOLEAN | T 日停牌状态 |
| name | VARCHAR(32) | T 日证券简称，可选 |
| source | VARCHAR(16) | 数据来源 |
| **PK** | (code, trade_date) | 含分区键 |

### stock_extra（按年分区）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | VARCHAR(10) | 证券代码 |
| trade_date | DATE | 分区键 |
| market_cap | NUMERIC(18,2) | T 日总市值（元） |
| float_cap | NUMERIC(18,2) | T 日流通市值（元） |
| turnover_rate | NUMERIC(8,3) | T 日换手率（%） |
| source | VARCHAR(16) | 数据来源 |
| **PK** | (code, trade_date) | 含分区键 |

### sector_daily

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sector_code | VARCHAR(32) | 东财板块代码 |
| sector_name | VARCHAR(64) | T 日板块名称 |
| trade_date | DATE | 交易日 |
| close | NUMERIC(12,3) | 板块指数收盘 |
| pct_chg | NUMERIC(8,3) | 涨跌幅（%） |
| limit_up_count | INT | 按 T 日历史成分自算的涨停家数 |
| source | VARCHAR(16) | 数据来源 |
| **PK** | (sector_code, trade_date) | |

### sector_membership_daily（历史板块成分）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sector_code | VARCHAR(32) | 东财板块代码 |
| code | VARCHAR(10) | 证券代码 |
| trade_date | DATE | 成分有效的交易日 |
| source | VARCHAR(16) | 数据来源 |
| **PK** | (sector_code, code, trade_date) | |

### market_daily（市场状态输入）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| trade_date | DATE PK | 交易日 |
| sh_close | NUMERIC(12,3) | 上证指数收盘 |
| sh_ma20 | NUMERIC(12,3) | 上证指数 20 日均线 |
| limit_up_count | INT | 当日全市场涨停家数，股票范围与策略股票池一致 |
| source | VARCHAR(16) | 数据来源 |

### signals

保留原字段 `id`、`code`、`name`、`trigger_date`、`buy_low`、`buy_high`、`stop_loss`、`score`、`sub_scores`、`suggest_pos`、`reason`、`sector`、`market_state`、`created_at`，并增加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| price_snapshot_id | BIGINT | 所用价格版本 |
| data_manifest_id | BIGINT | 所用输入数据清单 |
| buy_low_actual / buy_high_actual | NUMERIC(12,3) | actual 执行区间；`buy_low`/`buy_high` 保留 qfq 计算区间 |
| stop_loss_actual | NUMERIC(12,3) | actual 执行止损；`stop_loss` 保留 qfq 计算价 |
| params | JSONB | 策略参数快照 |
| data_quality | VARCHAR(16) | complete/partial |

### data_manifests

每次同步或计算输入生成一条清单，记录日期范围、源文件校验和、数据源、导入时间、覆盖/新增行数及校验状态。回测结果通过该清单和 `price_snapshot_id` 复现输入。

### backtest_runs

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGSERIAL PK | 回测 ID |
| run_at | TIMESTAMPTZ | 运行时间 |
| params | JSONB | 参数快照 |
| price_snapshot_id | BIGINT | 价格快照 |
| data_manifest_id | BIGINT | 输入数据清单 |
| metrics | JSONB | 汇总指标 |
| trade_log | JSONB | 逐笔结果，含未成交/延迟成交原因 |
| sample | VARCHAR(8) | in/out |
| status | VARCHAR(16) | complete/partial/invalid |

## 3. 分区原则

`daily_price`、`stock_status_daily`、`stock_extra` 按 `trade_date` 做 RANGE 年分区；主键必须包含分区键。查询价格时必须同时指定 `price_snapshot_id` 和日期范围。默认分区用于接收未来日期，运维任务应定期创建未来分区并检查默认分区是否意外积压。`sector_daily`、`sector_membership_daily`、`market_daily`、`signals`、`backtest_runs`、`price_snapshots`、`data_manifests` 暂不分区。

核心分区表约束示例：

```sql
CREATE TABLE daily_price (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  price_snapshot_id BIGINT NOT NULL,
  open NUMERIC(12,3), high NUMERIC(12,3),
  low NUMERIC(12,3), close NUMERIC(12,3),
  hfq_open NUMERIC(12,3), hfq_high NUMERIC(12,3),
  hfq_low NUMERIC(12,3), hfq_close NUMERIC(12,3),
  actual_open NUMERIC(12,3), actual_high NUMERIC(12,3),
  actual_low NUMERIC(12,3), actual_close NUMERIC(12,3),
  volume BIGINT, amount NUMERIC(18,2),
  adjustment_factor NUMERIC(20,10),
  source_price_basis VARCHAR(16), source VARCHAR(16),
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (code, trade_date, price_snapshot_id)
) PARTITION BY RANGE (trade_date);

CREATE TABLE stock_status_daily (
  code VARCHAR(10) NOT NULL,
  trade_date DATE NOT NULL,
  is_st BOOLEAN NOT NULL,
  is_suspended BOOLEAN NOT NULL,
  name VARCHAR(32), source VARCHAR(16),
  PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);
```

## 4. 回测完整性规则

- 回测每个交易日必须具备该日证券状态、股票池指标、板块成分和市场状态输入。缺项日期记为 `partial`，不纳入正式验收指标。
- 退市证券必须保留到历史数据中；不得仅用当前仍上市的股票列表生成历史股票池。
- 任何历史修订都生成新数据清单；不能改写已完成回测引用的输入版本。
