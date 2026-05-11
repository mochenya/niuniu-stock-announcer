from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.paths import (
    DEFAULT_WATCHLIST_FILE,
    PROJECT_ROOT,
    resolve_env_file,
    resolve_project_path,
)
from domain.config_models import (
    RuntimeConfig,
    TelegramChannelConfig,
    TelegramSettings,
)


class _RuntimeSettings(BaseSettings):
    """只负责从环境变量和 .env 读取原始配置值。"""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str | None = Field(
        None,
        validation_alias="WATCHLIST_DATABASE_URL",
    )
    watchlist_config_file: str | None = Field(
        None,
        validation_alias="WATCHLIST_CONFIG_FILE",
    )
    window_days: int = Field(
        3,
        validation_alias="WATCHLIST_WINDOW_DAYS",
    )
    sync_source_delay_seconds: str | None = Field(
        None,
        validation_alias="WATCHLIST_SYNC_SOURCE_DELAY_SECONDS",
    )
    pdf_save_dir: str | None = Field(
        None,
        validation_alias="PDF_SAVE_DIR",
    )
    llm_base_url: str = Field(
        "",
        validation_alias="LLM_BASE_URL",
    )
    llm_api_key: str = Field(
        "",
        validation_alias="LLM_API_KEY",
    )
    llm_model: str = Field(
        "",
        validation_alias="LLM_MODEL",
    )
    llm_temperature: float = Field(
        0,
        validation_alias="LLM_TEMPERATURE",
    )
    llm_timeout: float = Field(
        30,
        validation_alias="LLM_TIMEOUT",
    )
    llm_max_retries: int = Field(
        2,
        validation_alias="LLM_MAX_RETRIES",
    )
    telegram_timeout: float = Field(
        30,
        validation_alias="TELEGRAM_TIMEOUT",
    )
    telegram_a_share_bot_token: str = Field(
        "",
        validation_alias="TELEGRAM_A_SHARE_BOT_TOKEN",
    )
    telegram_a_share_topic_url: str = Field(
        "",
        validation_alias="TELEGRAM_A_SHARE_TOPIC_URL",
    )
    telegram_hk_bot_token: str = Field(
        "",
        validation_alias="TELEGRAM_HK_BOT_TOKEN",
    )
    telegram_hk_topic_url: str = Field(
        "",
        validation_alias="TELEGRAM_HK_TOPIC_URL",
    )


def load_runtime_config(
    *,
    env_file: str | Path | None = None,
    require_database: bool = False,
    require_llm: bool = False,
    require_telegram: bool = False,
) -> RuntimeConfig:
    """从 .env 和环境变量加载运行配置，并按调用场景校验必填项。"""
    resolved_env_file = resolve_env_file(env_file)
    settings = _RuntimeSettings(
        _env_file=resolved_env_file,
        _env_file_encoding="utf-8",
    )

    watchlist_file = resolve_project_path(
        settings.watchlist_config_file,
        default=DEFAULT_WATCHLIST_FILE,
        base_dir=resolved_env_file.parent,
    )
    pdf_save_dir = resolve_project_path(
        settings.pdf_save_dir,
        default=PROJECT_ROOT / "data" / "pdf",
        base_dir=resolved_env_file.parent,
    )
    config = RuntimeConfig(
        database_url=settings.database_url,
        watchlist_file=watchlist_file,
        window_days=settings.window_days,
        sync_source_delay_seconds=settings.sync_source_delay_seconds,
        pdf_save_dir=pdf_save_dir,
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model,
        llm_temperature=settings.llm_temperature,
        llm_timeout=settings.llm_timeout,
        llm_max_retries=settings.llm_max_retries,
        telegram=TelegramSettings(
            timeout=settings.telegram_timeout,
            a_share=TelegramChannelConfig(
                bot_token=settings.telegram_a_share_bot_token,
                topic_url=settings.telegram_a_share_topic_url,
            ),
            hk=TelegramChannelConfig(
                bot_token=settings.telegram_hk_bot_token,
                topic_url=settings.telegram_hk_topic_url,
            ),
        ),
    )
    if require_database and not config.database_url:
        raise ValueError("WATCHLIST_DATABASE_URL cannot be empty")
    if require_llm:
        _require_fields(
            (
                ("LLM_BASE_URL", config.llm_base_url),
                ("LLM_API_KEY", config.llm_api_key),
                ("LLM_MODEL", config.llm_model),
            )
        )
    if require_telegram:
        _require_fields(
            (
                ("TELEGRAM_A_SHARE_BOT_TOKEN", config.telegram.a_share.bot_token),
                ("TELEGRAM_A_SHARE_TOPIC_URL", config.telegram.a_share.topic_url),
                ("TELEGRAM_HK_BOT_TOKEN", config.telegram.hk.bot_token),
                ("TELEGRAM_HK_TOPIC_URL", config.telegram.hk.topic_url),
            )
        )
    return config


def _require_fields(fields: tuple[tuple[str, str], ...]) -> None:
    """统一生成缺失配置项的错误信息。"""
    missing_fields = [name for name, value in fields if not value]
    if missing_fields:
        raise ValueError(f"missing config: {', '.join(missing_fields)}")
