"""China provider-neutral 公告 Repository。"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.errors import (
    PersistenceConflictError,
    RecordNotFoundError,
)
from niuniu_stock_announcer.db.model.china import ChinaAnnouncementModel
from niuniu_stock_announcer.db.repositories.china._mapping import (
    map_china_announcement,
)
from niuniu_stock_announcer.db.schema import (
    ChinaAnnouncementRecord,
    ChinaAnnouncementWrite,
    PdfSnapshot,
)


class ChinaAnnouncementRepository:
    """在调用方 Session 内读写 China 公告，不拥有事务提交。"""

    def __init__(self, session: Session) -> None:
        """绑定一个 UnitOfWork 的 Session。

        Args:
            session: 当前短事务唯一使用的 SQLAlchemy Session。
        """
        self._session = session

    def upsert(self, value: ChinaAnnouncementWrite) -> ChinaAnnouncementRecord:
        """插入公告或刷新同一 Provider 身份的业务投影。

        `market_scope` 是同一业务身份的稳定事实；冲突时拒绝改变它。已下载 PDF 三字段由
        专用方法管理，重复发现不能清空或覆盖。

        Args:
            value: 已在 Provider mapper 边界校验的公告事实。

        Returns:
            脱离 ORM 的最新公告记录。

        Raises:
            PersistenceConflictError: 同一 Provider 身份出现不同 scope。
        """
        values = value.model_dump(mode="python")
        statement = (
            insert(ChinaAnnouncementModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["provider_key", "provider_announcement_id"]
            )
            .returning(ChinaAnnouncementModel)
        )
        model = self._session.scalars(statement).one_or_none()
        if model is not None:
            return map_china_announcement(model)

        model = self._session.scalar(
            select(ChinaAnnouncementModel)
            .where(
                ChinaAnnouncementModel.provider_key == value.provider_key,
                ChinaAnnouncementModel.provider_announcement_id
                == value.provider_announcement_id,
            )
            .with_for_update()
        )
        if model is None:
            raise RecordNotFoundError("公告冲突后未找到已存在记录")
        if model.market_scope != value.market_scope:
            raise PersistenceConflictError("同一公告身份出现不同 market_scope")

        for field in (
            "exchanges",
            "stock_codes",
            "stock_names",
            "title",
            "published_at",
            "source_url",
        ):
            setattr(model, field, values[field])
        model.last_seen_at = func.now()
        self._session.flush()
        return map_china_announcement(model)

    def get(self, announcement_id: int) -> ChinaAnnouncementRecord | None:
        """按内部 ID 读取公告。

        Args:
            announcement_id: China 公告内部 bigint ID。

        Returns:
            找到时返回冻结记录，否则返回 `None`。
        """
        model = self._session.get(ChinaAnnouncementModel, announcement_id)
        return None if model is None else map_china_announcement(model)

    def get_by_provider_identity(
        self, provider_key: str, provider_announcement_id: str
    ) -> ChinaAnnouncementRecord | None:
        """按 Provider 业务身份读取公告。

        Args:
            provider_key: `cninfo/sse/szse` 来源 key。
            provider_announcement_id: Provider adapter 生成的稳定公告 ID。

        Returns:
            找到时返回冻结记录，否则返回 `None`。
        """
        model = self._session.scalar(
            select(ChinaAnnouncementModel).where(
                ChinaAnnouncementModel.provider_key == provider_key,
                ChinaAnnouncementModel.provider_announcement_id
                == provider_announcement_id,
            )
        )
        return None if model is None else map_china_announcement(model)

    def attach_pdf(
        self, announcement_id: int, pdf: PdfSnapshot
    ) -> ChinaAnnouncementRecord:
        """首次写入已验证 PDF 三字段，并拒绝覆盖不同文件。

        Args:
            announcement_id: China 公告内部 ID。
            pdf: 已校验的相对路径、size 与 SHA-256。

        Returns:
            带 PDF 快照的冻结公告记录。

        Raises:
            RecordNotFoundError: 公告不存在。
            PersistenceConflictError: 已有 PDF 快照与本次值不同。
        """
        model = self._session.scalar(
            select(ChinaAnnouncementModel)
            .where(ChinaAnnouncementModel.id == announcement_id)
            .with_for_update()
        )
        if model is None:
            raise RecordNotFoundError(f"China 公告不存在: {announcement_id}")
        existing = map_china_announcement(model).pdf
        if existing is not None:
            if existing != pdf:
                raise PersistenceConflictError("公告 PDF 快照已存在且不一致")
            return map_china_announcement(model)
        model.pdf_storage_relative_path = pdf.storage_relative_path
        model.pdf_size_bytes = pdf.size_bytes
        model.pdf_sha256 = pdf.sha256
        self._session.flush()
        return map_china_announcement(model)
