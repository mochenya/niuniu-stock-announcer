"""China discovery match Repository。"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.errors import (
    PersistenceConflictError,
    RecordNotFoundError,
)
from niuniu_stock_announcer.db.model.china import ChinaAnnouncementMatchModel
from niuniu_stock_announcer.db.schema import (
    ChinaMatchPersistResult,
    ChinaMatchRecord,
    ChinaMatchWrite,
    TitleFilterDecision,
)


def _map_match(model: ChinaAnnouncementMatchModel) -> ChinaMatchRecord:
    return ChinaMatchRecord(
        id=model.id,
        china_announcement_id=model.china_announcement_id,
        plan_key=model.plan_key,
        discovery_type=model.discovery_type,
        market_scope=model.market_scope,
        query_exchange=model.query_exchange,
        query_stock_code=model.query_stock_code,
        query_provider_key=model.query_provider_key,
        matched_search_keywords=tuple(model.matched_search_keywords),
        filter_status=model.filter_status,
        filter_decisions=tuple(
            TitleFilterDecision.model_validate(item) for item in model.filter_decisions
        ),
        first_seen_at=model.first_seen_at,
        last_seen_at=model.last_seen_at,
        hit_count=model.hit_count,
    )


class ChinaMatchRepository:
    """冻结首次 match 决定并聚合后续重复发现证据。"""

    def __init__(self, session: Session) -> None:
        """绑定当前 UnitOfWork Session。

        Args:
            session: 当前短事务唯一使用的 Session。
        """
        self._session = session

    def record(self, value: ChinaMatchWrite) -> ChinaMatchPersistResult:
        """创建 match，或在一致上下文下增加 hit 和关键词证据。

        首次 `filter_status/filter_decisions/query` 是审计事实，普通重复发现不能重算覆盖；
        否则同一 Plan 的历史选择理由会随当前配置悄然漂移。

        Args:
            value: 本轮 discovery 和过滤产生的 typed match。

        Returns:
            最新 match 及是否由本次创建。

        Raises:
            PersistenceConflictError: 已有记录的冻结上下文与本次不一致。
        """
        values = value.model_dump(mode="python")
        values["matched_search_keywords"] = list(value.matched_search_keywords)
        values["filter_decisions"] = [
            decision.model_dump(mode="json") for decision in value.filter_decisions
        ]
        model = self._session.scalars(
            insert(ChinaAnnouncementMatchModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["china_announcement_id", "plan_key"]
            )
            .returning(ChinaAnnouncementMatchModel)
        ).one_or_none()
        if model is not None:
            return ChinaMatchPersistResult(record=_map_match(model), created=True)

        model = self._session.scalar(
            select(ChinaAnnouncementMatchModel)
            .where(
                ChinaAnnouncementMatchModel.china_announcement_id
                == value.china_announcement_id,
                ChinaAnnouncementMatchModel.plan_key == value.plan_key,
            )
            .with_for_update()
        )
        if model is None:
            raise RecordNotFoundError("match 冲突后未找到已存在记录")
        existing = _map_match(model)
        frozen_fields = (
            "discovery_type",
            "market_scope",
            "query_exchange",
            "query_stock_code",
            "query_provider_key",
            "filter_status",
            "filter_decisions",
        )
        if any(
            getattr(existing, field) != getattr(value, field) for field in frozen_fields
        ):
            raise PersistenceConflictError("同一公告与 Plan 的首次 match 上下文不一致")

        model.matched_search_keywords = list(
            dict.fromkeys(
                [*model.matched_search_keywords, *value.matched_search_keywords]
            )
        )
        model.hit_count += 1
        model.last_seen_at = func.now()
        self._session.flush()
        return ChinaMatchPersistResult(record=_map_match(model), created=False)

    def get(self, announcement_id: int, plan_key: str) -> ChinaMatchRecord | None:
        """读取一个公告与 Plan 的唯一 match。

        Args:
            announcement_id: China 公告内部 ID。
            plan_key: 稳定 Plan 身份。

        Returns:
            找到时返回冻结记录，否则返回 `None`。
        """
        model = self._session.scalar(
            select(ChinaAnnouncementMatchModel).where(
                ChinaAnnouncementMatchModel.china_announcement_id == announcement_id,
                ChinaAnnouncementMatchModel.plan_key == plan_key,
            )
        )
        return None if model is None else _map_match(model)
