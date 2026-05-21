---
name: niuniu-stock-cli
description: "Use this skill whenever the user wants to add stocks, companies, or watchlist entries (精选个股池), add title exclude keywords (全局关键词/过滤词), or asks how to run niuniu-stock CLI commands. Trigger on: stock codes (600000, 00700), market identifiers (sh/sz/bj/hk), company names in stock context, phrases like '帮我加', '加进去', '关注这个股票', '加入watchlist', '过滤公告', '排除关键词', and workflow questions about sync/run/process-pending/retry-failed. Also trigger when the user mentions 精选个股池, 公告过滤, or 牛牛股票. Do not resolve company names to stock codes inside this skill."
---

# 牛牛股票 CLI 命令助手

用这个 skill 帮用户快速写出或执行 `niuniu-stock` 的正确 CLI 调用。回答要短，优先给命令，不要展开成完整手册。

所有命令都使用这个形式：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock ...
```

如果要实际执行，把 `{PROJECT_DIR}` 替换成当前项目路径。

## 优先处理：添加精选个股池股票

用户说"加进精选个股池"、"加入 watchlist"、"关注这个股票"、"帮我把 600000 加进去"、"把某公司加进去"时，优先理解为添加 `stocks`：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add stock {market} {code}
```

- `{market}` 只能是 `sh`、`sz`、`bj`、`hk`。
- `{code}` 是交易所原始股票代码字符串；港股要保留前导零。
- 用户只给公司名时，不要猜代码；说明需要先获得股票代码，或交给查询/联网能力处理。
- 用户只给代码但没给市场时，简短追问市场。

示例：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add stock sh 600000
uv run --directory {PROJECT_DIR} niuniu-stock config add stock sz 300750
uv run --directory {PROJECT_DIR} niuniu-stock config add stock hk 00700
```

## 添加全局标题排除关键词

用户说"过滤/排除某类公告标题"、"加全局关键词"、"不要看业绩说明会"时，用：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add global-keyword "{keyword}"
```

示例：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add global-keyword "业绩说明会"
```

## 是否执行命令

- 用户问"命令怎么写"或"怎么调用"：只输出命令。
- 用户明确说"帮我加"、"执行一下"、"跑一下"：可以执行对应命令。
- `config add stock` 会查询最近 60 天公告来校验股票；如果用户没有明确授权执行，就不要主动运行。
- reset 数据库、发送 Telegram、调用 LLM、下载 PDF 相关流程要先确认。

## 工作流命令速查

只在用户明确问工作流或运行流程时给出：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock sync
uv run --directory {PROJECT_DIR} niuniu-stock run
uv run --directory {PROJECT_DIR} niuniu-stock process-pending
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed summary
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed delivery
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed all
```

`init-db --reset --yes` 会删除并重建本地 workflow 表，只有用户明确确认后才能执行。

需要更精确的股票代码格式、命令用途或安全注意事项时，读取 `references/cli-commands.md`。
