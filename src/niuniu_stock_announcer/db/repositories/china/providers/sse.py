"""SSE 原始公告快照 Repository。"""

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.errors import (
    PersistenceConflictError,
    RecordNotFoundError,
)
from niuniu_stock_announcer.db.model.china import SseAnnouncementModel
from niuniu_stock_announcer.db.schema import SseAnnouncementRecord, SseAnnouncementWrite


class SseAnnouncementRepository:
    """在调用方事务内保存 SSE 最近来源快照。"""

    def __init__(self, session: Session) -> None:
        """绑定当前 UnitOfWork Session。

        Args:
            session: 当前短事务唯一使用的 Session。
        """
        self._session = session

    def upsert(self, value: SseAnnouncementWrite) -> SseAnnouncementRecord:
        """插入或刷新显式来源列，同时保持首次发现时间。

        Args:
            value: 已校验 SSE 来源快照。

        Returns:
            脱离 ORM 的最新来源记录。

        Raises:
            PersistenceConflictError: 同一来源身份被映射到另一 China 公告。
        """
        values = value.model_dump(mode="python")
        model = self._session.scalars(
            insert(SseAnnouncementModel)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(SseAnnouncementModel)
        ).one_or_none()
        if model is None:
            models = self._session.scalars(
                select(SseAnnouncementModel)
                .where(
                    or_(
                        SseAnnouncementModel.provider_announcement_id
                        == value.provider_announcement_id,
                        SseAnnouncementModel.china_announcement_id
                        == value.china_announcement_id,
                    )
                )
                .order_by(SseAnnouncementModel.id)
                .with_for_update()
            ).all()
            if not models:
                raise RecordNotFoundError("SSE 冲突后未找到来源记录")
            if len(models) != 1:
                raise PersistenceConflictError(
                    "SSE 来源身份与 China 公告分别命中不同记录"
                )
            model = models[0]
            if (
                model.china_announcement_id != value.china_announcement_id
                or model.provider_announcement_id != value.provider_announcement_id
            ):
                raise PersistenceConflictError(
                    "SSE 来源身份与 China 公告一对一映射冲突"
                )
            for field, field_value in values.items():
                if field != "china_announcement_id":
                    setattr(model, field, field_value)
            model.last_seen_at = func.now()
            self._session.flush()
        return SseAnnouncementRecord.model_validate(model)
