# v2 CLI 参考

所有命令使用：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock ...
```

## Plan

```bash
uv run --directory {PROJECT_DIR} niuniu-stock plan validate --plan /path/to/plan.yaml
```

`--plan` 必须出现恰好一次，值必须是普通 `.yaml`/`.yml` 文件。没有默认 Plan、目录扫描、glob、
Plan 列表或 `run-all`。Plan 由部署管理，不提供旧 `config add` 编辑器。

## Discovery 与运行

```bash
uv run --directory {PROJECT_DIR} niuniu-stock sync --plan /path/to/plan.yaml
uv run --directory {PROJECT_DIR} niuniu-stock run --plan /path/to/plan.yaml
```

`sync` 只执行 discovery；`run` 继续处理本轮新 selected match/delivery activation。两份 Plan
由 scheduler 分别调用，不能合并为一个隐式命令。

## 恢复

```bash
uv run --directory {PROJECT_DIR} niuniu-stock process-pending
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed summary
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed telegram
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed all
```

这些命令不接受 Plan；`process-pending` 只领取 `pending`，retry 只领取确定 `failed`，`unknown`
永不自动重发。

## 数据库

```bash
uv run --directory {PROJECT_DIR} niuniu-stock db current
uv run --directory {PROJECT_DIR} niuniu-stock db upgrade
```

`db upgrade` 使用 `DATABASE_URL` 执行 Alembic migration。v2 CLI 不提供 reset/drop 或旧 `init-db`。
