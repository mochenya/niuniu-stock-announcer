# 牛牛公告员

> **NiuNiu Stock Announcer** — 本地化的 A 股 / 港股公告监控与智能摘要推送工具

[![Python 3.14+](https://img.shields.io/badge/Python-3.14+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **多市场覆盖** | 沪（sh）、深（sz）、北（bj）、港（hk）四大交易所 |
| **灵活数据源** | 上交所 / 深交所 / 巨潮资讯，按市场可配置切换 |
| **关键词过滤** | 全局 + 个股级别的标题排除关键词，精准筛选 |
| **PDF 智能摘要** | PyMuPDF 提取 + LLM 结构化总结，生成摘要文本与标签 |
| **Telegram 推送** | A 股 / 港股分别投递到不同话题，支持文本 + PDF 附件 |
| **完善的状态管理** | PostgreSQL 持久化，自动恢复异常状态，支持失败重试 |
| **运行日志通知** | 可选将每次运行结果推送到 Telegram，便于监控 |

## 快速开始

### 环境要求

- Python >= 3.14
- PostgreSQL
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
# 克隆项目
git clone <your-repo-url>
cd niuniu-stock-announcer

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，填写数据库、LLM、Telegram 等配置

# 配置监控股票列表
cp config/watchlist.example.yaml config/watchlist.yaml
# 编辑 watchlist.yaml，添加你关注的股票

# 初始化数据库
uv run niuniu-stock init-db
```

### 运行

```bash
# 手动执行一次完整工作流（同步 + 摘要 + 推送）
uv run niuniu-stock run

# 仅同步公告（不做摘要和推送）
uv run niuniu-stock sync

# 处理数据库中待处理的摘要和推送
uv run niuniu-stock process-pending

# 重试失败的任务
uv run niuniu-stock retry-failed all
```

## 使用指南

### 管理监控股票

```bash
# 添加股票到监列表（自动验证是否在交易所可查）
uv run niuniu-stock config add stock sh 600000
uv run niuniu-stock config add stock hk 00700

# 添加全局排除关键词
uv run niuniu-stock config add global-keyword "业绩预告"
```

### 命令一览

| 命令 | 说明 |
|------|------|
| `init-db` | 初始化数据库表结构 |
| `init-db --reset --yes` | ⚠️ 重置数据库（会删除所有数据） |
| `sync` | 同步公告到数据库 |
| `run` | 完整工作流：同步 → 摘要 → 推送 |
| `process-pending` | 处理待处理的摘要和推送任务 |
| `retry-failed summary` | 重试失败的摘要任务 |
| `retry-failed delivery` | 重试失败的推送任务 |
| `retry-failed all` | 重试所有失败任务（摘要耗尽后转为直发 PDF） |
| `config add stock <市场> <代码>` | 添加监控股票 |
| `config add global-keyword <关键词>` | 添加全局排除关键词 |

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.14+ |
| CLI 框架 | Typer |
| 数据库 | PostgreSQL + psycopg 3 |
| 数据模型 | Pydantic v2 |
| PDF 提取 | PyMuPDF (pymupdf4llm) |
| LLM 集成 | OpenAI 兼容 API |
| 消息推送 | python-telegram-bot |
| 日志 | Loguru |
| 代码规范 | Ruff |

> 详细架构和项目结构说明见 [docs/architecture.md](docs/architecture.md)

## 配置说明

项目使用两个配置文件：

- **`.env`**：数据库连接、LLM API、Telegram Bot Token 等敏感配置
- **`config/watchlist.yaml`**：监控股票列表、数据源路由、过滤规则

> 完整配置参考见 [docs/configuration.md](docs/configuration.md)

## 安全提示

- `.env`、`config/watchlist.yaml`、API Key、Telegram Bot Token 等均为本地配置，**请勿提交到版本控制**
- `init-db --reset --yes` 会删除所有数据，请谨慎使用
- Telegram 推送会发送实际消息，请确认配置正确

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
