# MODULE_INTERFACE

**项目**：stocktool
**版本**：v1.0 / 2026-10-23

## 1. 数据层

```python
data.tdx_parser.parse_txt(path: str) -> pd.DataFrame
# 列: code,name,date,open,high,low,close,volume,amount,adj_type='hfq_raw'
data.tdx_parser.to_qfq(df) -> pd.DataFrame          # 转前复权口径
data.ak_sync.sync_sector(conn, start: date, end: date) -> int
data.ak_sync.sync_extra(conn, trade_date: date) -> int
data.sync.run(mode: Literal['incremental','full_qfq'], conn) -> SyncReport
data.validate.check(conn, trade_date: date) -> list[Issue]

## 2. 引擎层

engine.universe.build(conn, trade_date: date) -> pd.DataFrame
# code,name,market_cap,float_cap,turnover_ma20,amount_ma20,...

engine.sector.mainline(conn, trade_date: date, params: SectorParams) -> pd.DataFrame
# sector_code,sector_name,rank5d,lu_count

engine.signal.generate(conn, trade_date: date, params: SignalParams) -> pd.DataFrame
# code,name,buy_low,buy_high,stop_loss,score,sub_scores,suggest_pos,reason,sector,market_state

engine.score.compute(row, ctx) -> tuple[int, dict]

engine.backtest.run(conn, start: date, end: date, params: dict) -> BacktestResult
# BacktestResult: trades: DataFrame, metrics: dict, sample: 'in'|'out'

## 3. API 层

GET  /health                  -> {"status":"ok","db":"ok"}
GET  /signals?date=YYYY-MM-DD -> [{code,name,buy_low,...,score}]
POST /backtest/run            -> {"run_id":int}    # 异步任务，写 backtest_runs
GET  /backtest/{id}           -> {params,metrics,sample}

## 4. 约定

所有函数接收 conn（psycopg 连接）或 engine；
日期用 datetime.date；DataFrame 列名全小写蛇形；
参数用 @dataclass（SignalParams/SectorParams），字段即参数表；
异常：DataMissingError、ParamInvalidError，API 层转 HTTP 4xx/5xx。
```
