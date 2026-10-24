# MODULE_INTERFACE

**项目**：stocktool
**版本**：v1.1 / 2026-10-24

## 1. 数据层

    data.tdx_parser.parse_txt(path: str) -> pd.DataFrame
    # code,name,date,hfq_open,hfq_high,hfq_low,hfq_close,volume,amount,source_price_basis

    data.tdx_parser.to_qfq(df, adjustment_factors, adjustment_basis_date: date) -> pd.DataFrame
    # 输出 actual OHLC、qfq OHLC、adjustment_factor；因子来源必须可验证

    data.ak_sync.sync_sector(conn, start: date, end: date) -> int
    data.ak_sync.sync_sector_membership(conn, start: date, end: date) -> int
    data.ak_sync.sync_extra(conn, trade_date: date) -> int
    data.ak_sync.sync_market(conn, start: date, end: date) -> int
    data.sync.run(mode: Literal['incremental', 'full_qfq'], conn) -> SyncReport
    # SyncReport 包含 data_manifest_id、price_snapshot_id、counts、issues
    data.validate.check(conn, trade_date: date) -> list[Issue]

full_qfq 必须创建新 price_snapshot_id，不能覆盖已有快照；缺少可信复权因子或关键历史时点数据时，标记数据不完整并阻止正式回测。

## 2. 引擎层

    engine.universe.build(conn, trade_date: date, data_manifest_id: int) -> pd.DataFrame
    # code,name,market_cap,float_cap,turnover_ma20,amount_ma20,is_st,is_suspended

    engine.sector.mainline(conn, trade_date: date, params: SectorParams, data_manifest_id: int) -> pd.DataFrame
    # sector_code,sector_name,rank5d,lu_count

    engine.signal.generate(
        conn, trade_date: date, params: SignalParams,
        price_snapshot_id: int, data_manifest_id: int
    ) -> pd.DataFrame
    # code,name,buy_low,buy_high,stop_loss,buy_low_actual,buy_high_actual,stop_loss_actual,
    # score,sub_scores,suggest_pos,reason,sector,market_state

    engine.score.compute(row, ctx) -> tuple[int, dict]

    engine.backtest.run(
        conn, start: date, end: date, params: BacktestParams,
        price_snapshot_id: int, data_manifest_id: int
    ) -> BacktestResult
    # BacktestResult: trades: DataFrame, metrics: dict, sample: 'in'|'out', status: str

所有历史计算按 trade_date 查询历史状态和板块成分，不得联接当前证券/板块状态。信号与回测结果必须记录参数和输入版本。BacktestParams 的字段与 STRATEGY_SPEC.md 中固定口径一致；仅将明确列为可调的参数暴露给优化流程。

## 3. API 层

    GET  /health                  -> {status: ok, db: ok}
    GET  /signals?date=YYYY-MM-DD -> [{code,name,buy_low,...,score,price_snapshot_id,data_quality}]
    POST /backtest/run            -> {run_id: int}
    GET  /backtest/{id}           -> {params,metrics,sample,status,price_snapshot_id,data_manifest_id}

## 4. 约定

- 所有函数显式接收 conn（psycopg 连接）或 engine；日期使用 datetime.date。
- DataFrame 列名使用全小写 snake_case；策略参数使用 @dataclass。
- 数据不完整、参数无效分别使用 DataMissingError、ParamInvalidError；API 层转换为 HTTP 4xx/5xx。
- 正式回测只接受 price_snapshot.status=complete 且输入数据完整的版本；试算可以返回 partial，但不得混入正式验收统计。
