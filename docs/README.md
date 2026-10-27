# stocktool

**A股主线回调波段选股工具（个人自用）**
版本：v1.0 / 2026-10-23

## 简介

盘后运行的 B/S 选股工具。基于"主线板块内异动股缩量回踩10日线"策略，每日输出 0—100 评分、建议买入区间、止损位、建议仓位；含回测模块验证期望值。**不自动交易、不对外服务、不提供投顾。**

## 技术栈

Python 3.14.6 + venv + Git / PostgreSQL（按年分区）/ FastAPI / Streamlit / AkShare / 通达信 TXT 导入

## 快速开始

```bash
cd /path/to/stocktool
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # 填 PG 连接串
docker compose up -d postgres        # 启动本地 PostgreSQL（宿主端口 15433）
bash scripts/init_db.sh              # 建库建表+按年分区
bash scripts/start.sh                # 启动 API(8000)+Web(8501)
bash scripts/close.sh                # 关闭 API+Web（不停止 PG）
docker compose stop postgres         # 停止 PostgreSQL，数据保留在 named volume
