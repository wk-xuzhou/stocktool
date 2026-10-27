# ARCHITECTURE

**项目**：stocktool
**版本**：v1.1 / 2026-10-24

## 1. 架构图（文字）

Windows PC(通达信) --TXT--> macOS data/raw/
|
data/sync.py (解析+AkShare+快照清单)
|
PostgreSQL(按年分区，历史状态/板块成分按日保存)
|
engine/universe->sector->signal->backtest
|
FastAPI(:8000) -- Streamlit(:8501)

## 2. 模块分层

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 数据层 | `data/` | `tdx_parser.py` `ak_sync.py` `sync.py` `validate.py`；actual/qfq 配对快照、数据清单、市场状态与历史板块成分 |
| 引擎层 | `engine/` | `universe.py` `sector.py` `signal.py` `score.py` `backtest.py` |
| API 层 | `api/` | `main.py`（`/health` `/signals` `/backtest/{id}`） |
| 展示层 | `dashboard/` | `app.py` |
| 脚本 | `scripts/` | `init_db.sh` `start.sh` `close.sh` `daily_run.sh` `init_docs.sh` |

## 3. start.sh / close.sh 设计

**start.sh**：

1. 加载 `.env`；
2. `pg_isready` 检查 PG；
3. `uvicorn api.main:app --port 8000 &`，PID 写 `run/api.pid`；
4. 轮询 `/health` 最多 30s；
5. `streamlit run dashboard/app.py --server.port 8501 &`，PID 写 `run/web.pid`；
6. 打印访问地址；失败则调 `close.sh` 回滚。

**close.sh**：读 `run/*.pid`，`kill` 后 `wait`，删 PID；缺 PID 则 `pkill -f` 兜底；不杀 PG（数据服务独立）。

**路径原则**：所有脚本首行用 `PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` 自推导，不硬编码绝对路径。

## 4. 关键设计决策

- **价格口径**：actual 用于涨跌停和成交，qfq 用于技术计算；优先直接导入可追溯 qfq 并与 actual 按代码/交易日配对。后复权转 qfq 仅在可验证因子或公司行为/算法资料存在时启用。
- **价格版本**：每次新建 actual/qfq 输入组合或转换结果生成不可变 `price_snapshot_id`，信号和回测保存输入版本；不静默覆盖已引用快照。
- **历史时点**：ST、停牌、退市状态和板块成分按交易日保存，回测禁止使用当前状态回看历史。
- **分区**：`daily_price`/`stock_status_daily`/`stock_extra` 按 `trade_date` RANGE 年分区，主键含分区键。
- **板块唯一源**：AkShare 东财板块日线，不依赖通达信板块导出。
- **回测主口径**：T+1 开盘价；实盘另记实际成交价与理论开盘价对照。
- **失败降级**：AkShare 失败跳过当日补字段，信号引擎用已有数据继续并标注数据完整性。
- **完整性边界**：缺少关键历史输入时可以生成 `partial` 试算，但不得进入正式回测和验收指标。
