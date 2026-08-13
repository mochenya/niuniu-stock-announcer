---
name: niuniu-stock-cli
description: "Use this skill whenever the user asks how to validate or run niuniu-stock China Plan commands, or asks about selected stocks, keyword discovery, announcement filtering, sync/run/recovery. Trigger on plan YAML, plan_key, stock codes (600000, 00700), market identifiers (sh/sz/bj/hk), phrases like '精选个股池', '公告过滤', 'process-pending', or 'retry-failed'. Do not edit deployed Plan or resolve company names to stock codes inside this skill."
---

# 牛牛股票 CLI 命令助手

用这个 skill 给用户写出 `niuniu-stock` 的 v2 命令。所有命令使用：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock ...
```

## Plan

v2 不提供旧 `config add` 编辑器。部署直接维护两种 Plan：`selected_stocks` 的 `stocks` 和
`market_keywords` 的 `discovery.search_keywords`；标题排除词位于 scope 的
`filters.title_exclude_keywords`。修改后先校验：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock plan validate --plan /path/to/plan.yaml
```

Plan 必须是恰好一个普通 `.yaml`/`.yml` 文件。没有默认 Plan、目录扫描、glob、Plan 列表或
`run-all`。真实 Plan、`.env`、数据库和 scheduler 不由这个 skill 修改。

## 工作流

```bash
uv run --directory {PROJECT_DIR} niuniu-stock sync --plan /path/to/plan.yaml
uv run --directory {PROJECT_DIR} niuniu-stock run --plan /path/to/plan.yaml
uv run --directory {PROJECT_DIR} niuniu-stock process-pending
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed summary
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed telegram
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed all
uv run --directory {PROJECT_DIR} niuniu-stock db current
uv run --directory {PROJECT_DIR} niuniu-stock db upgrade
```

`sync`/`run` 会访问公告 Provider；`run` 可能下载 PDF、调用 LLM 和发送 Telegram，未获用户授权
不要主动执行。`process-pending` 只处理 `pending`；retry 只处理确定 `failed`，`unknown` 永不自动
重发。`db upgrade` 修改目标数据库，项目不提供 reset/drop 命令。
