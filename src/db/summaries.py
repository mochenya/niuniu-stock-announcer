from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from announcements.sources import (
    normalize_announcement_source,
)
from db.base import RepositoryBase
from db.queries import SUMMARY_CANDIDATE_SQL
from db.query_helpers import (
    append_limit,
    build_ref_clause,
    fetchall,
)
from db.records import SummaryCandidateRecord
from db.row_mappers import build_summary_candidate_record
from domain.common import (
    AnnouncementSource,
    SummaryStatus,
)
from domain.summary_models import SummaryRunResult
from domain.workflow_models import (
    AnnouncementRef,
)


class SummaryRepository(RepositoryBase):
    """管理公告摘要阶段的候选查询和状态写入。"""

    def list_summary_candidates(
        self,
        *,
        refs: Sequence[AnnouncementRef] | None = None,
        statuses: Sequence[SummaryStatus] = ("pending",),
        limit: int | None = None,
    ) -> list[SummaryCandidateRecord]:
        """列出满足指定状态的摘要候选公告。

        refs 用于限制本轮新公告或指定公告；None 表示扫描该状态的全部候选。
        """
        where = ["s.status = ANY(%s)"]
        params: list[Any] = [list(statuses)]
        if refs is not None:
            ref_clause, ref_params = build_ref_clause("s", refs)
            where.append(ref_clause)
            params.extend(ref_params)
        query = SUMMARY_CANDIDATE_SQL + f" WHERE {' AND '.join(where)}"
        query += (
            " ORDER BY a.announcement_time_ms DESC NULLS LAST, a.announcement_id ASC"
        )
        query, params = append_limit(query, params, limit)
        return [
            build_summary_candidate_record(row)
            for row in fetchall(self._conn, query, params)
        ]

    def claim_summary_candidates(
        self,
        *,
        refs: Sequence[AnnouncementRef] | None = None,
        statuses: Sequence[SummaryStatus] = ("pending",),
        limit: int | None = None,
    ) -> list[SummaryCandidateRecord]:
        """原子领取摘要候选，并立即标记为 running。

        SELECT 和 UPDATE 放在同一个 SQL 里，配合 SKIP LOCKED 避免多个 workflow
        进程同时领取同一条公告后重复下载 PDF 或调用 LLM。
        """
        where = ["s.status = ANY(%s)"]
        params: list[Any] = [list(statuses)]
        if refs is not None:
            ref_clause, ref_params = build_ref_clause("s", refs)
            where.append(ref_clause)
            params.extend(ref_params)
        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT %s"
            params.append(max(limit, 0))
        query = f"""
        WITH picked AS (
            SELECT s.announcement_source, s.announcement_id
            FROM announcement_summaries AS s
            JOIN announcements AS a
              ON a.source = s.announcement_source
             AND a.announcement_id = s.announcement_id
            WHERE {" AND ".join(where)}
            ORDER BY a.announcement_time_ms DESC NULLS LAST, a.announcement_id ASC
            {limit_clause}
            FOR UPDATE OF s SKIP LOCKED
        ),
        claimed AS (
            UPDATE announcement_summaries AS s
            SET status = 'running',
                failure_reason = NULL,
                failure_log = NULL,
                summary_started_at = now(),
                updated_at = now()
            FROM picked
            WHERE s.announcement_source = picked.announcement_source
              AND s.announcement_id = picked.announcement_id
            RETURNING s.announcement_source, s.announcement_id
        )
        {SUMMARY_CANDIDATE_SQL}
        JOIN claimed AS c
          ON c.announcement_source = s.announcement_source
         AND c.announcement_id = s.announcement_id
        ORDER BY a.announcement_time_ms DESC NULLS LAST, a.announcement_id ASC
        """
        return [
            build_summary_candidate_record(row)
            for row in fetchall(self._conn, query, params)
        ]

    def reset_stale_running_summaries(self, *, timeout_minutes: int) -> int:
        """把长时间停留 running 的摘要记录转为 failed，供后续 retry 接管。"""
        cursor = self._conn.execute(
            """
            UPDATE announcement_summaries
            SET status = 'failed',
                failure_reason = %s,
                failure_log = NULL,
                updated_at = now()
            WHERE status = 'running'
              AND summary_started_at IS NOT NULL
              AND summary_started_at < now() - (%s * interval '1 minute')
            """,
            (
                f"stale running summary recovered after {timeout_minutes} minutes",
                timeout_minutes,
            ),
        )
        return cursor.rowcount

    def mark_summary_running(
        self,
        *,
        source: AnnouncementSource | str,
        announcement_id: str,
    ) -> None:
        """把摘要记录标记为 running，并清掉上一轮失败信息。"""
        self._conn.execute(
            """
            UPDATE announcement_summaries
            SET status = 'running',
                failure_reason = NULL,
                failure_log = NULL,
                summary_started_at = now(),
                updated_at = now()
            WHERE announcement_source = %s AND announcement_id = %s
            """,
            (normalize_announcement_source(source), announcement_id),
        )

    def save_summary_success(
        self,
        *,
        source: AnnouncementSource | str,
        announcement_id: str,
        result: SummaryRunResult,
        pdf_local_path: str | Path,
    ) -> None:
        """保存摘要成功结果，供后续投递阶段直接读取 summary_text/tags/PDF。"""
        self._conn.execute(
            """
            UPDATE announcement_summaries
            SET status = 'completed',
                pdf_local_path = %s,
                summary_model = %s,
                summarized_at = now(),
                failure_reason = NULL,
                failure_log = NULL,
                summary_json = %s,
                summary_text = %s,
                summary_tags = %s,
                llm_response_json = %s,
                input_tokens = %s,
                output_tokens = %s,
                updated_at = now()
            WHERE announcement_source = %s AND announcement_id = %s
            """,
            (
                str(pdf_local_path),
                result.llm_model,
                Jsonb(result.summary.model_dump(mode="json")),
                result.summary.summary,
                Jsonb(result.summary.tags),
                None
                if result.llm_response_json is None
                else Jsonb(result.llm_response_json),
                result.input_tokens,
                result.output_tokens,
                normalize_announcement_source(source),
                announcement_id,
            ),
        )

    def save_summary_failure(
        self,
        *,
        source: AnnouncementSource | str,
        announcement_id: str,
        failure_reason: str,
        failure_log: str,
        pdf_local_path: str | Path | None = None,
        increment_failure_count: bool = False,
    ) -> None:
        """记录摘要失败；若 PDF 已下载成功则保留本地路径，便于重试复用。

        increment_failure_count=True 时同步把失败次数 +1；正常 run 阶段不计数，
        只有 retry 路径下的失败需要累计，避免无限重试同一条摘要。
        """
        self._conn.execute(
            """
            UPDATE announcement_summaries
            SET status = 'failed',
                pdf_local_path = COALESCE(%s, pdf_local_path),
                failure_reason = %s,
                failure_log = %s,
                summary_failure_count = summary_failure_count
                    + CASE WHEN %s THEN 1 ELSE 0 END,
                updated_at = now()
            WHERE announcement_source = %s AND announcement_id = %s
            """,
            (
                None if pdf_local_path is None else str(pdf_local_path),
                failure_reason,
                failure_log,
                increment_failure_count,
                normalize_announcement_source(source),
                announcement_id,
            ),
        )

    def mark_summary_skipped(
        self,
        *,
        source: AnnouncementSource | str,
        announcement_id: str,
    ) -> None:
        """超过最大失败次数时把 failed 记录置为 skipped，让投递阶段走 PDF 降级。

        WHERE 限定 status='failed'，确保只有真正用尽重试预算的记录会被降级，
        防止误把 running/completed 行强制改写。
        """
        cursor = self._conn.execute(
            """
            UPDATE announcement_summaries
            SET status = 'skipped',
                updated_at = now()
            WHERE announcement_source = %s
              AND announcement_id = %s
              AND status = 'failed'
              AND pdf_local_path IS NOT NULL
            """,
            (normalize_announcement_source(source), announcement_id),
        )
        if cursor.rowcount == 0:
            raise LookupError(
                f"cannot skip summary in current status: {source}/{announcement_id}"
            )
