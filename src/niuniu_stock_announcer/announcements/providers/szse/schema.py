"""SZSE SDK 原生公告的严格边界 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SzseNativeAnnouncement(BaseModel):
    """镜像锁定 SDK `SZSEAnnouncementRecord` 的完整已知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None = None
    annId: int | str | None = None
    title: str | None = None
    content: str | None = None
    publishTime: str | None = None
    attachPath: str | None = None
    attachFormat: str | None = None
    attachSize: int | None = None
    secCode: list[str] = Field(default_factory=list)
    secName: list[str] = Field(default_factory=list)
    bondType: str | None = None
    bigIndustryCode: str | None = None
    bigCategoryId: str | None = None
    smallCategoryId: str | None = None
    channelCode: str | None = None
