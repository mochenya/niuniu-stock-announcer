"""从环境与 `.env` 加载基础设施设置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = Path(".env")


class AppSettings(BaseSettings):
    """保存不属于业务 Plan 的应用基础设施配置。

    字段允许在不相关命令中为空；composition root 在创建数据库、LLM 或 Telegram 组件前
    调用对应的 `require_*` 方法。这样 `plan validate` 不会因为本机没有生产凭据而失败。
    """

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    document_storage_root: Path = Field(
        default=Path("data/documents"), alias="DOCUMENT_STORAGE_ROOT"
    )
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_api_key: SecretStr | None = Field(default=None, alias="LLM_API_KEY")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0, ge=0, alias="LLM_TEMPERATURE")
    llm_timeout: float = Field(default=30, gt=0, alias="LLM_TIMEOUT")
    llm_max_retries: int = Field(default=2, ge=0, alias="LLM_MAX_RETRIES")
    summary_max_failures: int = Field(default=3, gt=0, alias="SUMMARY_MAX_FAILURES")
    summary_running_timeout_minutes: int = Field(
        default=120, gt=0, alias="SUMMARY_RUNNING_TIMEOUT_MINUTES"
    )
    telegram_running_timeout_minutes: int = Field(
        default=30, gt=0, alias="TELEGRAM_RUNNING_TIMEOUT_MINUTES"
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None, alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_timeout: float = Field(default=30, gt=0, alias="TELEGRAM_TIMEOUT")
    telegram_run_log_target: str | None = Field(
        default=None, alias="TELEGRAM_RUN_LOG_TARGET"
    )
    telegram_run_log_attach_file: bool = Field(
        default=True, alias="TELEGRAM_RUN_LOG_ATTACH_FILE"
    )
    provider_timeout: float = Field(default=30, gt=0, alias="PROVIDER_TIMEOUT")
    provider_retries: int = Field(default=2, ge=0, alias="PROVIDER_RETRIES")
    sync_source_delay_seconds: float = Field(
        default=0.5, ge=0, alias="SYNC_SOURCE_DELAY_SECONDS"
    )
    pdf_download_delay_seconds: tuple[float, float] = Field(
        default=(0.5, 0.8), alias="PDF_DOWNLOAD_DELAY_SECONDS"
    )
    log_directory: Path = Field(default=Path("logs/runs"), alias="LOG_DIRECTORY")

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        if value is None:
            return ""
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("telegram_run_log_target", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("pdf_download_delay_seconds", mode="before")
    @classmethod
    def _parse_pdf_delay(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            return (0.5, 0.8)
        separator = "~" if "~" in normalized else "-"
        parts = [part.strip() for part in normalized.split(separator, maxsplit=1)]
        if len(parts) == 1:
            delay = float(parts[0])
            return (delay, delay)
        return (float(parts[0]), float(parts[1]))

    @field_validator("pdf_download_delay_seconds")
    @classmethod
    def _validate_pdf_delay(cls, value: tuple[float, float]) -> tuple[float, float]:
        minimum, maximum = value
        if minimum < 0 or maximum < 0:
            raise ValueError("PDF_DOWNLOAD_DELAY_SECONDS 不能小于 0")
        if minimum > maximum:
            raise ValueError("PDF_DOWNLOAD_DELAY_SECONDS 的最小值不能大于最大值")
        return value

    def require_database_url(self) -> str:
        """返回数据库 URL，并在连接创建前报告缺失配置。

        Returns:
            可直接交给 SQLAlchemy Engine 的 PostgreSQL URL。

        Raises:
            ValueError: 未配置 `DATABASE_URL` 或值为空。
        """
        return _require_secret(self.database_url, "DATABASE_URL")

    def require_llm_api_key(self) -> str:
        """返回 LLM API key，并在 client 创建前报告缺失配置。

        Returns:
            仅供 LLM adapter 使用的原始 key。

        Raises:
            ValueError: 未配置 `LLM_API_KEY` 或值为空。
        """
        return _require_secret(self.llm_api_key, "LLM_API_KEY")

    def require_telegram_bot_token(self) -> str:
        """返回全局 Telegram Bot token，并在 Bot 创建前报告缺失配置。

        Returns:
            仅供 Telegram adapter 使用的原始 token。

        Raises:
            ValueError: 未配置 `TELEGRAM_BOT_TOKEN` 或值为空。
        """
        return _require_secret(self.telegram_bot_token, "TELEGRAM_BOT_TOKEN")


def load_app_settings(*, env_file: str | Path | None = None) -> AppSettings:
    """按“进程环境优先于 `.env`”的顺序加载应用设置。

    Args:
        env_file: 可选 `.env` 文件；省略时读取当前部署目录的 `.env`（若存在）。

    Returns:
        已冻结且会遮蔽 secret 的应用设置。
    """
    return AppSettings(_env_file=DEFAULT_ENV_FILE if env_file is None else env_file)


def _require_secret(value: SecretStr | None, field_name: str) -> str:
    if value is None or not value.get_secret_value().strip():
        raise ValueError(f"缺少配置 `{field_name}`")
    return value.get_secret_value()
