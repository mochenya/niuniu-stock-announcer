# 牛牛股票公告员

牛牛股票公告员（NiuNiu Stock Announcer）是一个本地 Python CLI，用 China Plan 监控沪、深、北、港
市场公告，按标题规则筛选，使用 PDF 与摘要 Agent 生成不可变投递 payload，并可靠地投递到 Telegram。

## 特性

- `selected_stocks` 精选个股和 `market_keywords` 全市场关键词两种独立 Plan。
- `sh/sz/bj/hk` 的 Provider 路由由 Plan 根 mapping 冻结，显式路由失败不自动 fallback。
- PostgreSQL + Alembic v2 schema，公告、match、摘要和 Telegram child 具有明确身份与恢复状态。
- 摘要与 Telegram 外部调用前提交 `running`；文本成功后才发送 document；timeout/network/stale 进入
  `unknown`，不自动重发。
- PDF 使用 storage root 下的规范相对路径、size 与 SHA-256 快照；Telegram 没有远程 URL fallback。

## 安装与配置

环境要求：Python `>=3.14`、PostgreSQL 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
cp .env.example .env
uv run niuniu-stock db upgrade
```

`.env` 只保存数据库、LLM、Telegram token、storage 和超时等基础设施设置。真实 Plan 由部署管理，
不要写入仓库；可以从两个示例开始：

```bash
cp config/plan.selected.example.yaml /path/to/china-selected-stocks.yaml
cp config/plan.keywords.example.yaml /path/to/china-market-keywords.yaml
```

Plan 根节点必须包含 `market: china`、稳定的 `plan_key`、正整数 `window_days` 和非空
`market_scopes`。环境引用只支持完整标量 `${ENV_NAME}`，进程环境优先于 `.env`。

## CLI

```bash
# 校验单份 Plan，不连接数据库或外部服务
uv run niuniu-stock plan validate --plan /path/to/china-selected-stocks.yaml

# 执行一份 Plan；scheduler 对多份 Plan 分别调用
uv run niuniu-stock sync --plan /path/to/china-selected-stocks.yaml
uv run niuniu-stock run --plan /path/to/china-selected-stocks.yaml

# 全局恢复：不接受 Plan，不领取 unknown
uv run niuniu-stock process-pending
uv run niuniu-stock retry-failed summary
uv run niuniu-stock retry-failed telegram
uv run niuniu-stock retry-failed all

# 数据库只提供显式 migration 入口
uv run niuniu-stock db current
uv run niuniu-stock db upgrade
```

`sync` 只执行 discovery。`run` 只处理本轮新 selected match/delivery activation；已有公告被另一份
Plan 命中时复用共享摘要，但仍可创建当前 Plan 的新 target delivery。target 和 payload 创建后不可变，
修改 Plan 不会重发历史记录。

## Scheduler

[`scripts/run_workflow.sh`](scripts/run_workflow.sh) 使用一把 `flock` 互斥锁，依次执行
`RUN_WORKFLOW_SELECTED_PLAN_FILE` 与 `RUN_WORKFLOW_KEYWORD_PLAN_FILE` 指定的两份 Plan，最后默认
执行一次 `process-pending`。它只读取部署目录中的真实 `.env` 和 Plan；切换时应先停止旧 scheduler，
避免新旧系统并行发送。

## 架构与安全

[`docs/architecture.md`](docs/architecture.md) 说明唯一 `bootstrap.py` composition root、China Pipeline、
Stage、Service、Agent、IM adapter 和 ORM/Repository 边界。默认 `uv run pytest -q` 完全离线；真实
Provider/PDF 测试必须显式设置 `NIUNIU_RUN_LIVE_TESTS=1 -m live`。本仓库不自动调用真实 LLM、发送
Telegram 或修改部署数据库、`.env`、Plan 和 scheduler。

## 质量检查

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
```

详见 [LICENSE](LICENSE)。
