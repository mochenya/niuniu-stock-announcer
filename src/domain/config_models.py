from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.common import (
    AnnouncementSource,
    DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET,
    Market,
    build_stock_key,
    validate_announcement_source_for_market,
    normalize_optional_text,
    normalize_required_text,
    normalize_text,
    normalize_text_list,
)

DEFAULT_PDF_DOWNLOAD_DELAY_SECONDS = (0.5, 0.8)


class FilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("title_exclude_keywords", mode="before")
    @classmethod
    def _normalize_keywords(cls, value: object) -> list[str]:
        return normalize_text_list(value, field_name="title_exclude_keywords")


class StockConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Market
    code: str
    name: str | None = None
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("code", mode="before")
    @classmethod
    def _normalize_code(cls, value: object) -> str:
        return normalize_required_text(value, field_name="code")

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str | None:
        return normalize_optional_text(value, field_name="name")

    @field_validator("keywords", "exclude_keywords", mode="before")
    @classmethod
    def _normalize_keyword_lists(cls, value: object, info) -> list[str]:
        return normalize_text_list(value, field_name=info.field_name)

    @property
    def stock_key(self) -> str:
        return build_stock_key(market=self.market, stock_code=self.code)


class AnnouncementSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sh: AnnouncementSource = DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET["sh"]
    sz: AnnouncementSource = DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET["sz"]
    bj: AnnouncementSource = DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET["bj"]
    hk: AnnouncementSource = DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET["hk"]

    @field_validator("sh", "sz", "bj", "hk", mode="before")
    @classmethod
    def _validate_market_source(cls, value: object, info):
        market = info.field_name
        return validate_announcement_source_for_market(
            market=market,
            source=value,
        )

    def source_for_market(self, market: Market) -> AnnouncementSource:
        return getattr(self, market)


class TelegramDeliverySendPdfConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a_share: bool = True
    hk: bool = True


class WatchlistTelegramDeliveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    send_pdf: TelegramDeliverySendPdfConfig = Field(
        default_factory=TelegramDeliverySendPdfConfig
    )


class WatchlistConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_days: int | None = None
    sources: AnnouncementSourceConfig = Field(default_factory=AnnouncementSourceConfig)
    telegram_delivery: WatchlistTelegramDeliveryConfig = Field(
        default_factory=WatchlistTelegramDeliveryConfig
    )
    filters: FilterConfig = Field(default_factory=FilterConfig)
    stocks: list[StockConfig] = Field(default_factory=list)

    @field_validator("window_days")
    @classmethod
    def _validate_window_days(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("window_days must be greater than 0")
        return value

    @model_validator(mode="after")
    def _validate_stocks(self) -> WatchlistConfig:
        if not self.stocks:
            raise ValueError("stocks cannot be empty")
        return self


class TelegramChannelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_token: str = ""
    topic_url: str = ""

    @field_validator("bot_token", "topic_url", mode="before")
    @classmethod
    def _normalize_text_fields(cls, value: object, info) -> str:
        return normalize_text(value, field_name=info.field_name)


class TelegramSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout: float = 30
    a_share: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    hk: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("TELEGRAM_TIMEOUT must be greater than 0")
        return value


class TelegramRunLogSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    bot_token: str = ""
    target: str = ""
    attach_file: bool = True

    @field_validator("bot_token", "target", mode="before")
    @classmethod
    def _normalize_text_fields(cls, value: object, info) -> str:
        return normalize_text(value, field_name=info.field_name)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: str | None = None
    watchlist_file: Path
    window_days: int = 3
    sync_source_delay_seconds: float | None = None
    pdf_save_dir: Path = Path("data/pdf")
    pdf_download_delay_seconds: tuple[float, float] = DEFAULT_PDF_DOWNLOAD_DELAY_SECONDS
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0
    llm_timeout: float = 30
    llm_max_retries: int = 2
    summary_max_failures: int = 3
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    telegram_run_log: TelegramRunLogSettings = Field(
        default_factory=TelegramRunLogSettings
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> str | None:
        return normalize_optional_text(value, field_name="WATCHLIST_DATABASE_URL")

    @field_validator("window_days")
    @classmethod
    def _validate_window_days(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("WATCHLIST_WINDOW_DAYS must be greater than 0")
        return value

    @field_validator("sync_source_delay_seconds", mode="before")
    @classmethod
    def _normalize_sync_source_delay_seconds(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("sync_source_delay_seconds")
    @classmethod
    def _validate_sync_source_delay_seconds(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError(
                "WATCHLIST_SYNC_SOURCE_DELAY_SECONDS must be greater than or equal to 0"
            )
        return value

    @field_validator("pdf_download_delay_seconds", mode="before")
    @classmethod
    def _normalize_pdf_download_delay_seconds(
        cls,
        value: object,
    ) -> tuple[float, float] | object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return DEFAULT_PDF_DOWNLOAD_DELAY_SECONDS
            separator = "~" if "~" in normalized else "-"
            parts = [part.strip() for part in normalized.split(separator, maxsplit=1)]
            if len(parts) == 2:
                return (float(parts[0]), float(parts[1]))
            return (float(normalized), float(normalized))
        if isinstance(value, int | float):
            delay = float(value)
            return (delay, delay)
        return value

    @field_validator("pdf_download_delay_seconds")
    @classmethod
    def _validate_pdf_download_delay_seconds(
        cls,
        value: tuple[float, float],
    ) -> tuple[float, float]:
        min_delay, max_delay = value
        if min_delay < 0 or max_delay < 0:
            raise ValueError(
                "WATCHLIST_PDF_DOWNLOAD_DELAY_SECONDS must be greater than or equal to 0"
            )
        if min_delay > max_delay:
            raise ValueError(
                "WATCHLIST_PDF_DOWNLOAD_DELAY_SECONDS min cannot be greater than max"
            )
        return value

    @field_validator("llm_base_url", "llm_api_key", "llm_model", mode="before")
    @classmethod
    def _normalize_llm_text_fields(cls, value: object, info) -> str:
        return normalize_text(value, field_name=info.field_name)

    @field_validator("llm_temperature")
    @classmethod
    def _validate_llm_temperature(cls, value: float) -> float:
        if value < 0:
            raise ValueError("LLM_TEMPERATURE must be greater than or equal to 0")
        return value

    @field_validator("llm_timeout")
    @classmethod
    def _validate_llm_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("LLM_TIMEOUT must be greater than 0")
        return value

    @field_validator("llm_max_retries")
    @classmethod
    def _validate_llm_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("LLM_MAX_RETRIES must be greater than or equal to 0")
        return value

    @field_validator("summary_max_failures")
    @classmethod
    def _validate_summary_max_failures(cls, value: int) -> int:
        if value < 1:
            raise ValueError("WATCHLIST_SUMMARY_MAX_FAILURES must be greater than 0")
        return value
