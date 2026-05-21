# niuniu-stock CLI 命令参考

本参考只补充必要细节。对用户回复时保持简洁，优先给最短可用命令。

## 通用调用形式

```bash
uv run --directory {PROJECT_DIR} niuniu-stock ...
```

在当前项目实际执行时，将 `{PROJECT_DIR}` 替换为项目绝对路径。

## config add stock

用于把股票加入精选个股池，也就是 watchlist YAML 的 `stocks` 列表。

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add stock {market} {code}
```

### market

只能使用以下值：

- `sh`：上交所 / 上海市场
- `sz`：深交所 / 深圳市场
- `bj`：北交所 / 北京市场
- `hk`：港股

不要传中文市场名、交易所全称或 `SH`/`SZ` 前缀；需要转换成上面的短代码。

### code

`code` 使用交易所原始股票代码字符串，重点是保留前导零。

- A 股通常是 6 位数字，例如 `600000`、`000001`、`300750`。
- 北交所通常是 6 位数字，例如 `430047`、`835185`。
- 港股使用 5 位数字，必须保留前导零，例如 `00700`、`03690`。

不要这样传：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add stock sh sh:600000
uv run --directory {PROJECT_DIR} niuniu-stock config add stock hk 700
```

应该这样传：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add stock sh 600000
uv run --directory {PROJECT_DIR} niuniu-stock config add stock hk 00700
```

### 公司名和代码缺失

用户可能说"把腾讯加进去"或"把某公司加入精选个股池"。这时本 skill 应该触发，但不要在这里猜股票代码。

合适回复：

```text
需要先确认股票代码和市场。拿到代码后命令格式是：
uv run --directory {PROJECT_DIR} niuniu-stock config add stock {market} {code}
```

如果有其他查询公司代码的 skill、联网搜索或用户提供了代码，再继续生成 `config add stock` 命令。

### 执行行为

`config add stock` 会：

1. 读取 watchlist YAML。
2. 追加股票，默认写入空的 `keywords` 和 `exclude_keywords`。
3. 校验配置。
4. 按市场对应公告源查询最近 60 天公告。
5. 查询成功后保存配置；查询失败则保持配置不变。

因此用户只是问命令时不要执行；用户明确要求"帮我加/执行"时可以执行。

## config add global-keyword

用于添加全局标题排除关键词，写入 `filters.title_exclude_keywords`。

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add global-keyword "{keyword}"
```

示例：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config add global-keyword "业绩说明会"
uv run --directory {PROJECT_DIR} niuniu-stock config add global-keyword "回购注销"
```

关键词包含空格、中文标点或 shell 特殊字符时，用引号包住。

## 指定配置文件

一般不要主动加这些选项。只有用户明确要求使用非默认 `.env` 或 watchlist 文件时才使用：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock config --env-file .env add stock sh 600000
uv run --directory {PROJECT_DIR} niuniu-stock config --config-file config/watchlist.yaml add stock sh 600000
```

注意：Typer 组选项应放在 `config` 后、`add` 前。

## 工作流命令

### init-db

初始化数据库表：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock init-db
```

危险命令：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock init-db --reset --yes
```

`--reset --yes` 会删除 workflow 表并重建，必须先得到用户明确确认。

### sync

同步公告并写入待处理记录：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock sync
```

会访问外部公告源，必要时下载/解析远端数据；执行前需要用户明确授权。

### run

完整流程：先同步，再处理本次新种下的记录，包括摘要和投递：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock run
```

可能访问公告源、下载 PDF、调用 LLM、发送 Telegram；执行前需要用户明确授权。

### process-pending

处理数据库里已有的 pending summary 和 delivery：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock process-pending
```

可能调用 LLM 和发送 Telegram；执行前需要用户明确授权。

### retry-failed

重试失败任务：

```bash
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed summary
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed delivery
uv run --directory {PROJECT_DIR} niuniu-stock retry-failed all
```

- `summary`：重试摘要失败记录，可能调用 LLM。
- `delivery`：重试投递失败记录，可能发送 Telegram。
- `all`：依次重试摘要和投递。

## 回复风格

- 优先输出一条命令。
- 缺参数时只问缺的参数。
- 不解释 CLI 内部实现，除非用户问为什么。
- 不把多个命令堆给用户，除非用户明确要工作流速查。
