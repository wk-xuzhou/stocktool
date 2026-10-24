# DECISIONS

**项目**：stocktool
**版本**：v1.1 / 2026-10-24

| ID | 决策 | 理由 | 影响 |
| --- | --- | --- | --- |
| D01 | 板块动量唯一源=AkShare 东财 | 通达信无板块指数导出；东财口径统一 | sector.py 只读 sector_daily |
| D02 | daily_price/stock_status_daily/stock_extra 按年 RANGE 分区 | 8 年历史数据量和查询裁剪 | 主键含 trade_date；价格主键另含 price_snapshot_id |
| D03 | 回测主口径=T+1 开盘价 | 唯一可复现；涨停不可买可建模 | backtest 买入价固定 |
| D04 | 实盘记实际成交价+理论开盘价对照 | 验证限价区间是否优于开盘 | 跟踪表增列 |
| D05 | 后复权源转换为 qfq，并保留 actual 价格 | 技术指标与成交/涨跌停判断需要不同口径 | sync 层保存 qfq、actual、因子和来源版本 |
| D06 | 排除科创板/北交所/ST | 流动性与策略适配 | universe 过滤 |
| D07 | 盈亏比优先，止盈用移动止损 | 用户确认偏好 | 卖出规则见 STRATEGY_SPEC 第6节 |
| D08 | 市场状态双条件、取保守档、只降仓不空仓 | 降低震荡市连续止损 | signal 输出 market_state |
| D09 | 评分五因子权重 30/25/20/15/10 | 板块与回踩质量为主 | score.py |
| D10 | 不做实时/自动交易/对外服务 | 个人自用合规边界 | 范围外 |
| D11 | 历史计算使用 T 日状态和板块成分快照 | 避免当前状态回看历史造成前瞻偏差 | 新增 `stock_status_daily`、`sector_membership_daily` |
| D12 | qfq 价格按不可变快照版本保存 | 每周复权不能静默改写既有信号和回测 | 新增 `price_snapshots`，价格引用 `price_snapshot_id` |
| D13 | 仅凭后复权 OHLCV 不自动推导 qfq | 缺少可信复权因子时无法审计换算 | `to_qfq` 缺因子即失败 |
| D14 | 日线回测采用保守撮合优先级 | OHLC 无法还原盘中先后，必须固定可复现规则 | 规则见 `STRATEGY_SPEC.md` 第 6、9 节 |
| D15 | 确认正式评估的最小成交样本数 | 避免小样本结论误导 | 样本内至少 30 笔，样本外至少 10 笔 |
| D16 | 确认市值评分中心线为 100 亿元 | 作为流动性/市值因子的稳定参考点 | 按 `STRATEGY_SPEC.md` 计算市值适中度 |
