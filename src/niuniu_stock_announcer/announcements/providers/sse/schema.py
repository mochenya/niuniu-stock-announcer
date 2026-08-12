"""SSE SDK 原生公告的严格边界 Schema。"""

from pydantic import BaseModel, ConfigDict


class SseNativeAnnouncement(BaseModel):
    """镜像锁定 SDK `SSEBulletinFile` 的完整已知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    BULLETIN_TYPE_DESC: str | None = None
    BULLETIN_YEAR: str | None = None
    IS_HOLDER_DISCLOSE: str | None = None
    ORG_BULLETIN_ID: str | None = None
    ORG_FILE_TYPE: int | None = None
    SECURITY_CODE: str | None = None
    SECURITY_NAME: str | None = None
    SSEDATE: str | None = None
    TITLE: str | None = None
    URL: str | None = None
