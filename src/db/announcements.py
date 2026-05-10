from __future__ import annotations

from cninfo_announcement.models import BusinessAnnouncement
from psycopg.types.json import Jsonb

from announcements.sources import (
    normalize_announcement_source,
)
from db.base import RepositoryBase
from domain.search_models import (
    HitUpsertResult,
    SearchTask,
    TitleFilterDecision,
)
from domain.common import normalize_required_text
from domain.telegram_models import TelegramTargetKey


class AnnouncementWriteRepository(RepositoryBase):
    def upsert_announcement(self, announcement: BusinessAnnouncement) -> bool:
        """写入公告主表，并返回这次是否新增。

        workflow 依赖该返回值做同步统计；是否进入后续摘要阶段由命中记录决定。
        """
        announcement_id = _require_text(announcement.announcement_id, "announcement_id")
        source = normalize_announcement_source(announcement.source)
        # 这里依赖 PostgreSQL 的 xmax：为 0 表示本次是新插入，不是冲突更新。
        row = self._conn.execute(
            """
            INSERT INTO announcements (
                source,
                announcement_id,
                sec_code,
                sec_name,
                org_id,
                announcement_title,
                announcement_time_ms,
                adjunct_url,
                page_column
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, announcement_id) DO UPDATE SET
                sec_code = EXCLUDED.sec_code,
                sec_name = EXCLUDED.sec_name,
                org_id = EXCLUDED.org_id,
                announcement_title = EXCLUDED.announcement_title,
                announcement_time_ms = EXCLUDED.announcement_time_ms,
                adjunct_url = EXCLUDED.adjunct_url,
                page_column = EXCLUDED.page_column,
                updated_at = now()
            RETURNING (xmax = 0) AS inserted
            """,
            (
                source,
                announcement_id,
                announcement.sec_code,
                announcement.sec_name,
                announcement.org_id,
                announcement.announcement_title,
                announcement.announcement_time,
                announcement.adjunct_url,
                announcement.page_column,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("announcement upsert did not return a row")
        return bool(row[0])

    def upsert_hit(
        self,
        *,
        task: SearchTask,
        announcement: BusinessAnnouncement,
        decision: TitleFilterDecision,
    ) -> HitUpsertResult:
        """写入查询命中记录，并保留本次标题过滤决策。

        同一 source_key 再次命中同一公告只更新计数，不覆盖首次过滤依据。
        """
        announcement_id = _require_text(announcement.announcement_id, "announcement_id")
        filter_status = "filtered" if decision.filtered else "selected"
        # 冲突更新只刷新命中次数和时间，不覆盖首次命中时记录的过滤依据。
        row = self._conn.execute(
            """
            INSERT INTO announcement_hits (
                source_key,
                announcement_source,
                announcement_id,
                market,
                stock_code,
                stock_key,
                company_name,
                search_mode,
                search_keyword,
                filter_status,
                filter_reason,
                filter_keywords,
                filter_title,
                config_snapshot
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_key, announcement_source, announcement_id)
            DO UPDATE SET
                last_hit_at = now(),
                hit_count = announcement_hits.hit_count + 1,
                updated_at = now()
            RETURNING id, (xmax = 0) AS inserted, filter_status
            """,
            (
                task.source_key,
                task.announcement_source,
                announcement_id,
                task.market,
                task.stock_code,
                task.stock_key,
                announcement.sec_name,
                task.search_mode,
                task.search_keyword,
                filter_status,
                decision.reason,
                Jsonb(decision.matched_keywords),
                announcement.announcement_title,
                Jsonb(task.config_snapshot),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("hit upsert did not return a row")
        return HitUpsertResult(
            hit_id=int(row[0]),
            inserted=bool(row[1]),
            filter_status=str(row[2]),
        )

    def ensure_workflow_rows(
        self,
        *,
        hit_id: int,
        task: SearchTask,
        announcement: BusinessAnnouncement,
    ) -> bool:
        """为未过滤公告初始化一条摘要记录和一条 Telegram 投递记录。

        返回值只表示摘要行是否首次插入；投递行用唯一约束保证幂等。
        """
        announcement_id = _require_text(announcement.announcement_id, "announcement_id")
        source = normalize_announcement_source(announcement.source)
        company_name = _company_name(announcement, fallback=task.stock_code)
        target_key = _target_key_for_market(task.market)
        summary_row = self._conn.execute(
            """
            INSERT INTO announcement_summaries (
                announcement_source,
                announcement_id,
                primary_hit_id,
                stock_key,
                market,
                stock_code,
                company_name
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (announcement_source, announcement_id) DO NOTHING
            RETURNING announcement_id
            """,
            (
                source,
                announcement_id,
                hit_id,
                task.stock_key,
                task.market,
                task.stock_code,
                company_name,
            ),
        ).fetchone()
        self._conn.execute(
            """
            INSERT INTO telegram_deliveries (
                announcement_source,
                announcement_id,
                primary_hit_id,
                stock_key,
                market,
                stock_code,
                company_name,
                target_key
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (announcement_source, announcement_id, target_key) DO NOTHING
            """,
            (
                source,
                announcement_id,
                hit_id,
                task.stock_key,
                task.market,
                task.stock_code,
                company_name,
                target_key.value,
            ),
        )
        return summary_row is not None


def _target_key_for_market(market: str) -> TelegramTargetKey:
    if market == "hk":
        return TelegramTargetKey.HK
    return TelegramTargetKey.A_SHARE


def _company_name(announcement: BusinessAnnouncement, *, fallback: str) -> str:
    if announcement.sec_name and announcement.sec_name.strip():
        return announcement.sec_name.strip()
    return fallback


def _require_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    return normalize_required_text(value, field_name=field_name)
