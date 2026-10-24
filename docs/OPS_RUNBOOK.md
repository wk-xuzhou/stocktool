# OPS_RUNBOOK

**项目**：stocktool
**版本**：v1.1 / 2026-10-24

## 1. 每日流程（交易日 16:00 后）

1. Windows PC 通达信导出当日/近 5 日 TXT → 传至 macOS `data/raw/`；
2. `source .venv/bin/activate && bash scripts/daily_run.sh`：
   - `data.sync.run('incremental')`；
   - `data.validate.check`，失败写 `logs/error.log`；
   - `engine.signal.generate(today, price_snapshot_id, data_manifest_id)` 写 `signals`；
   - 导出 `output/signals_YYYYMMDD.csv`；
3. 打开 `http://localhost:8501` 查看信号；
4. 复制到股票软件自选，次日执行。

## 2. 每周流程

- `python -m data.sync --mode full_qfq` 生成新的前复权快照（防基准漂移，不覆盖已引用快照）；
- 检查各年分区行数、双源校验报告。
- 检查 `price_snapshot_id`、`data_manifest_id` 和关键历史输入完整性。

## 3. 启停

```bash
docker compose up -d postgres   # 启动本地 PostgreSQL（宿主端口 15433）
bash scripts/init_db.sh         # 首次运行或新增分区时初始化结构
bash scripts/start.sh           # 启 API+Web，PID 写 run/
bash scripts/close.sh           # 按 PID 关；不关 PG
docker compose stop postgres    # 停止 PostgreSQL，数据保留在 named volume
```

## 4. 故障处理

| 现象 | 处理 |
| --- | --- |
| AkShare 失败 | 跳过非关键补字段，信号标注 `partial`；缺少历史状态、板块成分或价格因子时阻止正式回测，次日重试 |
| PG 连接失败 | pg_isready；检查 .env；重启 PG |
| API 未就绪 | 看 logs/api.log；close.sh 后重 start.sh |
| 分区缺失 | 跑 init_db.sh 补建对应年分区 |
| 复权跳变告警 | 新建快照并隔离异常版本，查除权数据；不得覆盖已有正式结果引用的快照 |
