# v2 架构

牛牛股票公告员是本地 Python CLI。唯一生产包根是 `src/niuniu_stock_announcer/`，数据库是空的
PostgreSQL v2 schema，不读取旧 watchlist 或旧工作流记录。

## 数据流

```text
Plan YAML
  -> plan loader / typed China Plan
  -> ChinaPipeline
       -> discovery strategy -> Provider service -> SyncStage -> PostgreSQL
       -> SummaryStage -> Document Service -> China Agent -> summary terminal
       -> DeliveryStage -> immutable payload -> Telegram adapter
```

`bootstrap.py` 是唯一 composition root。CLI 只加载设置与唯一 Plan、调用 application、展示结果
并映射退出码；它不包含 SQL、Provider、PDF、LLM 或 Telegram 状态机。

## 身份与恢复

- Provider 公告以 `(provider_key, provider_announcement_id)` 唯一。
- match 以 `(china_announcement_id, plan_key)` 唯一；共享摘要属于公告，不属于 Plan。
- delivery 和已经物化的 summary/document payload 是不可变快照；target 变化不会重发历史记录。
- `run --plan` 只处理本轮新 selected match 的 summary/delivery activation，不能按公告总数推断。
- `process-pending` 只领取 `pending`；三个 `retry-failed` 入口只领取确定 `failed`；`unknown` 永不
  自动重发。
- 摘要外部调用前提交 `running`，Telegram child 文本先于 document，成功后分别保存外部 ID。
  timeout/network/stale 结果进入 `unknown`。

## 命令与运维

固定命令见 README。`plan validate` 不需要数据库、LLM 或 Telegram 配置；其余命令按实际副作用
懒加载配置。`scripts/run_workflow.sh` 使用一把互斥锁，依次执行两份明确 Plan，再可选调用一次
全局 `process-pending`。真实 `.env`、Plan、scheduler 部署和外部发送不由仓库测试修改。
