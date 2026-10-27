# MODULE_INTERFACE

**项目**：stocktool
**版本**：v1.2 / 2026-10-25

## 1. 数据层

    data.tdx_parser.parse_txt(path: str) -> pd.DataFrame
    # code,name,trade_date,actual_open/actual_high/actual_low/actual_close,
    # qfq_open/qfq_high/qfq_low/qfq_close,hfq_open/hfq_high/hfq_low/hfq_close,
    # volume,amount,source_price_basis
    # 按文件头识别 actual、qfq 或 hfq；未提供的口径列留空，保留原始来源口径。

    data.tdx_parser.to_qfq(df, adjustment_factors, adjustment_basis_date: date) -> pd.DataFrame
    # 可选转换路径：仅在 adjustment_factors、来源版本和基准规则可验证时生成 qfq；缺失即失败

    data.ak_sync.sync_sector(conn, start: date, end: date) -> int
    data.ak_sync.sync_sector_membership(conn, start: date, end: date) -> int
    data.ak_sync.sync_extra(conn, trade_date: date) -> int
    data.ak_sync.sync_market(conn, start: date, end: date) -> int
    data.sync.run(mode: Literal['full_actual', 'incremental', 'direct_qfq', 'full_qfq'], conn) -> SyncReport
    # SyncReport 包含 data_manifest_id、price_snapshot_id、counts、issues
    # full_actual 显式创建 actual 历史基线，仅允许 stocktool 数据库且拒绝 qfq/hfq 输入。
    # incremental 仅允许 stocktool 数据库且要求已有 actual 基线快照；复制基线快照后追加各代码最新日期之后的数据。
    # 空库历史全量导入是独立流程；incremental 不会自动把 raw 历史数据当作增量。
    data.validate.check(conn, trade_date: date) -> list[Issue]

    data.versioning.build_data_manifest(source, status='building') -> DataManifest
    data.versioning.persist_data_manifest(conn, manifest) -> int
    data.versioning.persist_price_snapshot(conn, snapshot) -> int
    # build_data_manifest 接受 SourceInput 或 Iterable[SourceInput]，逐文件消费，不保留已处理的数据帧。
    # 一个清单可聚合多个同来源、同版本、同价格口径文件；带逐文件 SHA-256、代码/日期范围、行数和状态；
    # 快照分别引用 actual/qfq/hfq 的 data_manifest_id。引用后的清单不可更新/删除。

    data.qfq_pair.scan_direct_qfq(actual_dir, qfq_dir) -> PairReport
    # 只校验文件集合、代码/交易日序列、成交量、成交额和源口径；
    # qfq/actual 的 OHLC 差异只统计不判错，不推导因子或 actual 执行价。

    data.sync.import_tdx_file(conn, path, price_snapshot_id: int, source='tdx') -> int
    data.sync.import_price_frame(conn, frame, price_snapshot_id: int, source='tdx') -> int
    # 单文件/单口径写入已建立的快照；重复 (code, trade_date, price_snapshot_id) 不覆盖。

2026-10-25 口径补充：解析器须从文件头识别 actual、qfq、hfq；actual 文件输出 `actual_open/actual_high/actual_low/actual_close`，qfq 文件输出 qfq 计算列，hfq 文件输出 `hfq_open/hfq_high/hfq_low/hfq_close`，非该口径的价格列留空，`source_price_basis` 记录源口径。三种口径都必须保留文件来源、代码、交易日和校验和；本批成交量已为股、成交额为元，不无条件乘 100。没有可信换算依据时只保留源价；不得利用收盘价比值补造其他价格列。详见 `DATA_SOURCE_CHECK.md`。

`direct_qfq` 必须记录 qfq 导出基准（若来源未声明则明确记为 unknown）、导出时间、来源、来源版本、原始文件校验和，并按代码/交易日与 actual 做配对校验。不得擅自补写具体基准规则。`full_qfq` 是可选转换路径，必须创建新 `price_snapshot_id`，不能覆盖已有快照；缺少可信复权因子或关键历史时点数据时，标记数据不完整并阻止 qfq 依赖的正式信号/回测。actual 解析、原始保存和 actual 查询不受该阻塞影响。

版本清单的构建和持久化接口只记录输入元数据，不执行行情导入、同步、配对判定或 qfq 转换；这些工作分别由 D07/D08/D12 完成。版本接口已接入 TXT 导入；actual 真实历史全量/增量/幂等验收已完成，输入规模与结果见 `docs/D08_ACTUAL_IMPORT_ACCEPTANCE.md`。跨输入完整性由 D12/D13 验收；qfq 和历史状态等来源缺口不阻止 actual 导入。

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

2026-10-27 D08 补充：批次导入首遍逐文件解析预检并构建清单，次遍逐文件解析写入；内存不随全部行情行数累积（仅文件元数据随文件数量增长）。解析前后及两遍之间均核验 SHA-256，源文件变化立即失败。调用方必须在同一事务中运行并在任何异常时回滚，禁止捕获异常后提交半套版本。无新增数据时不建版本，但空输入仍报告 `partial`/`invalid`，不伪报 `complete`。`full_actual` 每次显式运行创建独立快照；对同一快照重复写入不覆盖，完整批次跨快照去重不在当前范围。actual 导入状态 `complete` 仅表示本批文件齐备，不代表 qfq、历史状态或正式回测输入完整。
