"""Telegram adapter 的冻结输入与外部结果 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuniu_stock_announcer.storage.document import StorageRelativePath


def _require_nonblank_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("文本不能为空")
    return normalized


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TelegramTarget(_FrozenSchema):
    """保存 Bot API 使用的 chat 与可选 topic 地址。"""

    chat_id: int
    message_thread_id: int | None = None

    @model_validator(mode="after")
    def _validate_ids(self) -> TelegramTarget:
        if self.chat_id == 0:
            raise ValueError("Telegram chat_id 不能为 0")
        if self.message_thread_id is not None and self.message_thread_id <= 0:
            raise ValueError("Telegram message_thread_id 必须大于 0")
        return self


class TelegramTextSendRequest(_FrozenSchema):
    """描述一次只发送冻结 HTML 文本的请求。"""

    target: TelegramTarget
    text_content: str = Field(min_length=1)

    @field_validator("text_content")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _require_nonblank_text(value)


class TelegramDocumentSendRequest(_FrozenSchema):
    """描述一次只发送冻结本地 document 的请求。"""

    target: TelegramTarget
    storage_relative_path: StorageRelativePath
    document_filename: str = Field(min_length=1)
    document_size_bytes: int = Field(gt=0)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_caption: str

    @field_validator("document_filename")
    @classmethod
    def _normalize_filename(cls, value: str) -> str:
        return _require_nonblank_text(value)


class TelegramSendResult(_FrozenSchema):
    """保存 Telegram 已确认返回且可立即落库的外部身份。"""

    chat_id: int
    message_thread_id: int | None = None
    message_id: int = Field(gt=0)
    message_url: str | None = None

    @field_validator("message_url")
    @classmethod
    def _normalize_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_nonblank_text(value)
