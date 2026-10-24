# 仓库开发规范

## 项目结构

`stocktool` 是一个基于 Python 3.12、文档驱动的 A 股波段选股工具骨架。请确保各模块职责与文档中规定的分层严格一致：

- `data/`：通达信数据导入、AkShare 数据同步、数据校验以及原始/解析后数据目录。
- `engine/`：股票池（universe）、板块（sector）、交易信号（signal）、评分（scoring）和回测（backtest）逻辑。
- `api/`：FastAPI 接口服务；`dashboard/`：Streamlit 可视化界面。
- `tests/`：自动化测试；`scripts/`：运维脚本与辅助工具；`docs/`：系统架构、交易策略、数据字典、运维手册与测试计划等文档。

运行生成或仅限本地的内容存放在 `logs/`、`run/`、`output/`、`backup/`、`data/raw/` 和 `data/parsed/` 中；这些路径已被 Git 忽略。

## 构建、测试与开发

使用 `python3.12 -m venv .venv && source .venv/bin/activate` 创建文档推荐的虚拟环境，并通过 `pip install -r requirements.txt` 安装依赖。运行 `bash scripts/init_docs.sh` 可重新生成 `docs/README.md`。规划中的数据库及服务操作命令（`scripts/init_db.sh`、`scripts/start.sh`、`scripts/close.sh` 和 `scripts/daily_run.sh`）在 `docs/OPS_RUNBOOK.md` 中有详细描述，但目前尚未全部就绪；添加或更新相关脚本时请同步维护对应文档。

完成相关功能及测试实现后，可在仓库根目录下执行 `python -m pytest` 运行测试。如需进行轻量级的语法检查，可使用 `python -m compileall data engine api dashboard`。

## 编码风格与命名规范

使用 4 个空格缩进，采用表意清晰的 `snake_case`（蛇形命名），并保持函数精简与高可组合性。依照 `docs/MODULE_INTERFACE.md` 的规范，DataFrame 列名保持全小写 `snake_case`；日期字段使用 `datetime.date`；策略参数使用 `@dataclass` 对象封装。遵循已定义的层级边界，并显式传递数据库连接。当前项目尚未配置格式化工具或代码检查器（Linter），因此提交的代码应符合标准 Python 格式规范，且不得在未同步更新文档的情况下引入新的代码风格约定。

## 测试规范

测试文件命名遵循 `tests/test_<area>.py`，测试函数遵循 `test_<behavior>`。依照 `docs/TEST_PLAN.md`，测试应覆盖数据归一化、复权连续性、信号边界、仓位管理、交易成本处理以及防未来数据（前瞻偏差）保护等。测试必须使用确定性的固件（fixtures），除非明确标记为集成测试，否则严禁依赖实盘交易或外部网络服务。

## 提交与合并请求规范

使用简洁、祈使语气且符合 Conventional Commits 规范的前缀，例如 `docs:`、`feat:`、`fix:` 或 `test:`；现有历史提交记录范例为 `docs: v1.0 文档包`。Pull Request 应说明修改范围和设计影响，在适当时关联相关 Issue，列出验证命令及测试结果，并更新受影响的文档。如果涉及仪表盘（dashboard）变更，请附带截图；若涉及数据库或运行环境变更，请显式说明。

## 安全与配置

将敏感信息与密钥保留在 `.env` 中，严禁提交任何凭证或导出的行情数据；配置项发生变动时请同步更新 `.env.example`。本项目仅供个人非自动化辅助使用；在未重新评估文档中的系统范围与架构决策前，请勿添加实盘自动化交易或对外公网服务等功能。

## 其它需要遵守

1、称呼用户为“吴总”。
2、开发语言为中文，生成文档也为中文。
3、先构建测试，然后开发实现。
4、构建或修复代码后，必须运行测试，确保测试通过。
5、不要过度开发。
6、不要添加没必要的模块。
