"""应用对象组装入口。"""

from __future__ import annotations

from dataclasses import dataclass

from niuniu_stock_announcer.config.settings import AppSettings


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """保存命令执行期间共享的基础设施设置。

    这里暂不组装业务组件；后续 child 会按命令需要逐步加入具体依赖，避免基础阶段先创建
    没有调用方的抽象或外部 client。

    Attributes:
        settings: 已完成环境与 `.env` 覆盖解析的应用设置。
    """

    settings: AppSettings


def bootstrap(settings: AppSettings) -> ApplicationContext:
    """构造不触发数据库或网络连接的应用上下文。

    Args:
        settings: 已校验的基础设施设置；SecretStr 字段保持遮蔽状态。

    Returns:
        可由具体 CLI application 继续组装的基础上下文。
    """
    return ApplicationContext(settings=settings)
