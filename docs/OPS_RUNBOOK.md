# OPS_RUNBOOK

**项目**：stocktool
**版本**：v1.1 / 2026-10-24

## 1. 每日流程（交易日 16:00 后）

1. Windows PC 通达信导出当日/近 5 日 TXT → 传至 macOS `data/raw/`；
2. `source .venv/bin/activate && bash scripts/daily_run.sh`：
   - 确认连接 `stocktool` 数据库且已有 actual 基线快照后，运行 `data.sync.run('incremental')`；该模式只追加各代码最新日期之后的行，并复制上一快照形成完整新快照；无新增行时不建新版本；
   - `data.validate.check`，失败写 `logs/error.log`；
   - `engine.signal.generate(today, price_snapshot_id, data_manifest_id)` 写 `signals`；
   - 导出 `output/signals_YYYYMMDD.csv`；
3. 打开 `http://localhost:8501` 查看信号；
4. 复制到股票软件自选，次日执行。

## 2. 每周流程

- `python -m data.sync --mode direct_qfq` 导入带来源元数据的前复权快照；如有可审计因子，才使用 `python -m data.sync --mode full_qfq` 生成新的 qfq 快照（防基准漂移，不覆盖已引用快照）；actual 导入可独立运行。
- 首次历史导入与日常增量更新分开执行。当前 `incremental` 在找不到 `stocktool` 的 actual 基线快照时会拒绝运行；不要用其它项目数据库，也不要将全量 `data/raw/` 当成每日新增数据。
- 检查各年分区行数、双源校验报告。
- 检查 `price_snapshot_id`、`data_manifest_id` 和关键历史输入完整性。

### 2.1 显式 actual 历史全量导入（已实现的 Python 接口）

当前仅提供 Python 调用接口，`python -m data.sync --mode ...` CLI、`daily_run.sh`、信号和界面仍为规划能力，不应当作已实现入口。

```python
import os
from pathlib import Path

from dotenv import load_dotenv
import psycopg

from data.sync import run

load_dotenv()
# 本地 .env 配置 DATABASE_URI，指向本项目 stocktool 数据库；不在脚本中硬编码密码。
# 正常退出提交，异常退出自动回滚整个批次。
with psycopg.connect(os.environ["DATABASE_URI"]) as conn:
    report = run(
        "full_actual",
        conn,
        input_dir=Path("data/raw/export_no"),
        source_version="本次导出实际版本标识",
    )
    print(report)
```

- 这是显式全量操作，不由 `incremental` 自动触发；目录应指定到 actual 导出目录，不指向混合口径的 `data/raw/` 根目录。
- 首遍完整预检、次遍逐文件写入，不在内存保存整个历史批次；源文件在解析期间或两遍间变化会失败，须回滚后重试，不边导出边导入。
- 空文件保留在清单中：部分空为 `partial`、全部空为 `invalid`；actual 完整不表示正式信号/回测数据齐备。
- 重复写入同一快照不会覆盖数据；再次运行 `full_actual` 则显式建立新历史快照。增量沿用父快照并只追加更晚日期，同日修订仍需另行处理。
- 2026-10-27 已完成 `data/raw/export_no/` 实际 actual 基线导入与真实增量验收；固定快照 ID、计数、资源指标和 3 个空文件见 `docs/D08_ACTUAL_IMPORT_ACCEPTANCE.md`。重新初始化数据库或更换本地数据库后，应先确认当前快照状态，再按业务需要导入；不要重复执行历史全量任务创建不必要快照。

### 2.2 本地数据库集成验收

默认测试不连接数据库。先配置 `.env` 的 `DATABASE_URI` 并确认本地 PostgreSQL 已初始化，然后运行：

```bash
STOCKTOOL_RUN_DB_TESTS=1 .venv/bin/python -m pytest tests/test_sync_db.py -q
```

测试仅允许连接本地 `15433` 端口的 `stocktool`，使用合成行情验证全量基线、重复写入、增量复制、不覆盖旧日、无新增不建版本和中途失败的事务回滚。所有测试记录回滚；PostgreSQL 序列取号不会回滚，可能出现正常的 ID 间隔。测试期间不要并发运行其它导入任务。

## 3. 启停

```bash
docker compose up -d postgres   # 启动本地 PostgreSQL（宿主端口 15433）
bash scripts/init_db.sh         # 首次运行或新增分区时初始化结构；无宿主 psql 时使用容器内客户端
bash scripts/start.sh           # 启 API+Web，PID 写 run/
bash scripts/close.sh           # 按 PID 关；不关 PG
docker compose stop postgres    # 停止 PostgreSQL，数据保留在 named volume
```

## 4. 故障处理

| 现象 | 处理 |
| --- | --- |
| AkShare 失败 | 跳过非关键补字段，信号标注 `partial`；缺少历史状态、板块成分或可追溯 qfq 时阻止依赖该输入的正式信号/回测，actual 导入和格式校验仍可运行，次日重试 |
| PG 连接失败 | 检查 `docker compose ps` 和容器内 `pg_isready`；检查 .env；重启 PG |
| API 未就绪 | 看 logs/api.log；close.sh 后重 start.sh |
| 分区缺失 | 跑 init_db.sh 补建对应年分区 |
| 复权跳变告警 | 新建快照并隔离异常版本，查除权数据；不得覆盖已有正式结果引用的快照 |
