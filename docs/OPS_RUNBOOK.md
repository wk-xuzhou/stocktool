# OPS_RUNBOOK

**项目**：stocktool
**版本**：v1.0 / 2026-10-23

## 1. 每日流程（交易日 16:00 后）

1. Windows PC 通达信导出当日/近 5 日 TXT → 传至 macOS `data/raw/`；
2. `source .venv/bin/activate && bash scripts/daily_run.sh`：
   - `data.sync.run('incremental')`；
   - `data.validate.check`，失败写 `logs/error.log`；
   - `engine.signal.generate(today)` 写 `signals`；
   - 导出 `output/signals_YYYYMMDD.csv`；
3. 打开 `http://localhost:8501` 查看信号；
4. 复制到股票软件自选，次日执行。

## 2. 每周流程

- `python -m data.sync --mode full_qfq` 全量覆盖前复权历史（防基准漂移）；
- 检查各年分区行数、双源校验报告。

## 3. 启停

```bash
bash scripts/start.sh   # 启 API+Web，PID 写 run/
bash scripts/close.sh   # 按 PID 关；不关 PG

## 4. 故障处理

| 现象 | 处理 |
| --- | --- |
| AkShare 失败 | 跳过补字段，信号标注 data_partial，次日重试 |
| PG 连接失败 | pg_isready；检查 .env；重启 PG |
| API 未就绪 | 看 logs/api.log；close.sh 后重 start.sh |
| 分区缺失 | 跑 init_db.sh 补建对应年分区 |
| 复权跳变告警 | 全量覆盖该股历史，查除权数据 |
