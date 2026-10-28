# OPS_RUNBOOK

**项目**：stocktool
**版本**：v1.2 / 2026-10-28

## 1. 每日流程（北京时间 17:00 后）

当前已实现 actual 历史导入、TXT 增量导入和 AkShare 自动获取日线；`daily_run.sh`、校验报告、正式信号、API 与仪表盘尚未实现。现阶段每日操作如下：

1. 确认 PostgreSQL 已启动并完成 `bash scripts/init_db.sh`，数据库内已有 actual 历史基线。
2. 执行 `scripts/akshare_daily_sync.sh`，或安装下述 macOS 定时任务。
3. 查看命令 JSON 报告或 `logs/akshare-daily.log`：核对 `rows_inserted`、`symbols_failed`、`symbols_empty`、`symbols_stale`、快照 ID 和状态；`partial` 不代表全部证券已更新到最新交易日。
4. Windows 通达信导出可继续用 `data.sync.run("incremental", ...)` 补 actual 行情；增量仅存新行，通过父快照继承历史，不再每日复制完整历史；无新增不建版本。

### 1.1 AkShare 自动行情更新

已提供盘后 actual 日线同步命令：

```bash
scripts/akshare_daily_sync.sh
```

命令只允许北京时间 17:00 后运行，从 AkShare 当前证券列表选择沪深主板和创业板代码，再逐证券请求不复权日线；新证券最多回看 90 个自然日。默认每次请求间隔 0.2 秒、证券列表和单证券失败最多重试 3 次，连续 10 个证券失败则整批退出并回滚；失败证券会报告，成功证券可写入 `partial` 快照，下次运行按各证券末日继续补齐。成交量按来源“手”转换为股，并以成交额/成交量和 OHLC 区间做一致性校验。原始响应保存在忽略目录 `data/raw/akshare_daily/`。

如返回日线末日早于运行日（周末按周五检查），记为 `symbols_stale`，状态 `partial`；当前没有可信节假日/停牌日历，因此节假日与停牌也会保守报告新鲜度未确认，不能把旧行情称为最新。`complete` 仅表示本批请求及上述检查通过，不代表历史输入或正式信号数据齐备。同日修订不会覆盖已有行情。

全市场逐证券请求可能持续数十分钟，依赖本机网络和 AkShare 上游可用性。同步通过 PostgreSQL advisory lock 防止重入；macOS 工作日自动运行模板位于 `scripts/launchd/com.stocktool.akshare-daily.plist`，安装后按本机时间周一至周五 17:30 运行（已核实当前 macOS 系统时区为 Asia/Shanghai）：

```bash
mkdir -p ~/Library/LaunchAgents logs
cp scripts/launchd/com.stocktool.akshare-daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stocktool.akshare-daily.plist
launchctl print gui/$(id -u)/com.stocktool.akshare-daily
```

模板使用 launchd 的系统本地时间；若系统时区改为 America/New_York，须将 Hour 改为 5（对应北京时间 17:30/18:30），重新加载任务。模板按当前仓库绝对路径 `/Users/wukui/SourceCode/stocktool` 配置，迁移目录时须同步修改。卸载命令：

```bash
launchctl bootout gui/$(id -u)/com.stocktool.akshare-daily
```

运行要求当前用户登录、Python 虚拟环境和 PostgreSQL 服务可用。睡眠期间错过的日历触发可能在唤醒时合并补触发；关机/退出登录期间不保证补跑，应手动检查并补跑。无新增行不创建快照；节假日仍可能返回 partial 新鲜度提示。AkShare 自动更新只补 actual 行情，不补历史 ST/停牌/板块成分或 qfq，不能据此直接运行正式信号/回测。

### 1.2 外部接口验收状态

2026-10-28：90 项离线测试、3 项本地 PostgreSQL 合成集成测试通过；定时任务已安装到 `~/Library/LaunchAgents/com.stocktool.akshare-daily.plist` 并加载，当前 runs=0，尚未触发抓取。真实证券列表和 `000001` 少量日线请求均遇到代理连接错误，未成功取得真实响应，未写入真实新行情。成交量“手→股”的实现尚待真实同日 TDX/AkShare 核对；不能将模板已具备或启动项已加载表述为“最新行情已入库”。历史实际基线仍为 snapshot 17 / manifest 16，末日 2026-10-23。首次成功同步后应复核报告和数据库末日，再登记真实验收。

## 2. 每周流程

- 规划流程（尚无 CLI，且受 P02 阻塞）：`python -m data.sync --mode direct_qfq` 导入带来源元数据的前复权快照；如有可审计因子，才使用 `python -m data.sync --mode full_qfq` 生成新的 qfq 快照（防基准漂移，不覆盖已引用快照）；actual 导入可独立运行。
- 首次历史导入与日常增量更新分开执行。当前 `incremental` 在找不到 `stocktool` 的 actual 基线快照时会拒绝运行；不要用其它项目数据库，也不要将全量 `data/raw/` 当成每日新增数据。
- 检查各年分区行数、双源校验报告。
- 检查 `price_snapshot_id`、`data_manifest_id` 和关键历史输入完整性。

### 2.1 显式 actual 历史全量导入（已实现的 Python 接口）

TXT 导入当前仅提供 Python 调用接口，`python -m data.sync --mode ...` CLI、`daily_run.sh`、信号和界面仍为规划能力。AkShare 实际可用入口为 `python -m data.ak_sync` 或 `scripts/akshare_daily_sync.sh`。

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

尚未创建 `.env` 时，可用 `set -a; source .env.example; set +a` 显式加载本地开发配置再测试；不在命令输出连接串。测试仅允许连接本地 `15433` 端口的 `stocktool`，使用合成行情验证全量基线、重复写入、增量继承、多层父快照解析、不覆盖旧日、无新增不建版本和中途失败的事务回滚。所有测试记录回滚；PostgreSQL 序列取号不会回滚，可能出现正常的 ID 间隔。测试期间不要并发运行其它导入任务。

## 3. 启停

下面数据库启动与初始化命令已实现；`start.sh`/`close.sh` 为后续 A04 计划，当前不可执行。

```bash
docker compose up -d postgres   # 启动本地 PostgreSQL（宿主端口 15433）
bash scripts/init_db.sh         # 首次运行或新增分区时初始化结构；无宿主 psql 时使用容器内客户端
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
