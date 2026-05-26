from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from db.base import RepositoryBase
from db.queries import DELIVERY_CANDIDATE_SQL
from db.query_helpers import (
    append_limit,
    build_ref_clause,
    fetchall,
)
from db.row_mappers import build_workflow_candidate
from domain.common import WorkflowStatus
from domain.workflow_models import (
    AnnouncementRef,
    WorkflowCandidate,
)


class TelegramDeliveryRepository(RepositoryBase):
    """管理 Telegram 投递阶段的候选查询和状态写入。"""

    def list_delivery_candidates(
        self,
        *,
        refs: Sequence[AnnouncementRef] | None = None,
        statuses: Sequence[WorkflowStatus] = ("pending",),
        limit: int | None = None,
    ) -> list[WorkflowCandidate]:
        """只返回摘要可用、PDF 已下载的投递候选。

        摘要要么是 completed 且字段齐全，要么是 skipped——后者意味着 LLM 多次
        失败已放弃，投递阶段会以纯 PDF 降级方式发送。两类候选都强制要求本地
        PDF 存在。
        """
        where = [
            "s.pdf_local_path IS NOT NULL",
            "("
            "(s.status = 'completed'"
            " AND s.summary_text IS NOT NULL"
            " AND jsonb_array_length(COALESCE(s.summary_tags, '[]'::jsonb))"
            " BETWEEN 3 AND 6)"
            " OR s.status = 'skipped'"
            ")",
            "d.status = ANY(%s)",
        ]
        params: list[Any] = [list(statuses)]
        if refs is not None:
            ref_clause, ref_params = build_ref_clause("d", refs)
            where.append(ref_clause)
            params.extend(ref_params)
        query = DELIVERY_CANDIDATE_SQL + f" WHERE {' AND '.join(where)}"
        query += (
            " ORDER BY a.announcement_time_ms ASC NULLS FIRST, a.announcement_id ASC"
        )
        query, params = append_limit(query, params, limit)
        return [
            build_workflow_candidate(row) for row in fetchall(self._conn, query, params)
        ]

    def mark_delivery_running(self, *, delivery_id: int) -> None:
        """把投递记录标记为 running，并清掉上一轮失败信息。"""
        self._conn.execute(
            """
            UPDATE telegram_deliveries
            SET status = 'running',
                failure_reason = NULL,
                failure_log = NULL,
                started_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (delivery_id,),
        )

    def save_text_message(
        self,
        *,
        delivery_id: int,
        chat_id: int,
        message_thread_id: int,
        message_id: int,
    ) -> None:
        """保存文本消息 ID；重试时据此跳过已确认成功的文本发送。"""
        self._conn.execute(
            """
            UPDATE telegram_deliveries
            SET target_chat_id = %s,
                target_message_thread_id = %s,
                text_message_id = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (chat_id, message_thread_id, message_id, delivery_id),
        )

    def save_pdf_message(
        self,
        *,
        delivery_id: int,
        chat_id: int,
        message_thread_id: int,
        message_id: int,
    ) -> None:
        """保存 PDF 消息 ID；重试时据此跳过已确认成功的文件发送。"""
        self._conn.execute(
            """
            UPDATE telegram_deliveries
            SET target_chat_id = %s,
                target_message_thread_id = %s,
                pdf_message_id = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (chat_id, message_thread_id, message_id, delivery_id),
        )

    def mark_delivery_completed(self, *, delivery_id: int) -> None:
        """文本和 PDF 均处理完成后，标记整条投递记录成功。"""
        self._conn.execute(
            """
            UPDATE telegram_deliveries
            SET status = 'completed',
                failure_reason = NULL,
                failure_log = NULL,
                sent_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (delivery_id,),
        )

    def save_delivery_failure(
        self,
        *,
        delivery_id: int,
        status: WorkflowStatus,
        failure_reason: str,
        failure_log: str,
    ) -> None:
        """保存 failed 或 unknown 投递状态及诊断信息。"""
        self._conn.execute(
            """
            UPDATE telegram_deliveries
            SET status = %s,
                failure_reason = %s,
                failure_log = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (status, failure_reason, failure_log, delivery_id),
        )
