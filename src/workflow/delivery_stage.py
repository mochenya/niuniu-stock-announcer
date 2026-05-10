from __future__ import annotations

import traceback
from collections.abc import Sequence
from pathlib import Path

from db.repository import AnnouncementRepository
from delivery.telegram.sender import (
    TelegramSendOutcomeUnknown,
    send_telegram_delivery,
)
from domain.common import WorkflowStatus
from domain.config_models import RuntimeConfig
from domain.telegram_models import (
    TelegramSendResult,
    TelegramSummaryPayload,
)
from domain.workflow_models import (
    PipelineStageSummary,
    WorkflowCandidate,
)
from log.events import log_event
from workflow.common import (
    ProgressReporter,
    candidate_log_fields,
    format_progress,
    noop_progress,
    short_error,
)


def run_delivery_candidates(
    repo: AnnouncementRepository,
    *,
    conn,
    candidates: Sequence[WorkflowCandidate],
    runtime_config: RuntimeConfig,
    progress: ProgressReporter | None = None,
) -> PipelineStageSummary:
    """依次处理 Telegram 投递候选，并汇总 completed/failed/unknown。

    调用方负责只传入摘要和 PDF 已就绪的候选，避免投递阶段再做复杂筛选。
    """
    report = progress or noop_progress
    result = PipelineStageSummary(candidate_count=len(candidates))
    report(log_event("delivery", "running", total=len(candidates)))
    for index, candidate in enumerate(candidates, start=1):
        status = _run_delivery_candidate(
            repo,
            conn=conn,
            candidate=candidate,
            runtime_config=runtime_config,
            progress=report,
            index=index,
            total=len(candidates),
        )
        if status == "completed":
            result.completed_count += 1
        elif status == "unknown":
            result.unknown_count += 1
        else:
            result.failed_count += 1
    report(
        log_event(
            "delivery",
            "finished",
            completed=result.completed_count,
            failed=result.failed_count,
            unknown=result.unknown_count,
        )
    )
    return result


def _run_delivery_candidate(
    repo: AnnouncementRepository,
    *,
    conn,
    candidate: WorkflowCandidate,
    runtime_config: RuntimeConfig,
    progress: ProgressReporter,
    index: int,
    total: int,
) -> WorkflowStatus:
    """处理单条 Telegram 投递候选。

    文本和 PDF 发送结果会分步提交；若发送过程结果不可确认，记录为 unknown。
    """
    item_progress = format_progress(index, total)
    progress(
        log_event(
            "delivery",
            "processing",
            progress=item_progress,
            target=candidate.target_key,
            **candidate_log_fields(candidate),
        )
    )
    delivery_id = _require_delivery_id(candidate)
    try:
        payload = _build_telegram_payload(candidate)
        pdf_path = _require_pdf_path(candidate)
        repo.mark_delivery_running(delivery_id=delivery_id)
        conn.commit()

        def save_text_result(result: TelegramSendResult) -> None:
            # 文本和 PDF 分步提交，重试时可以跳过已经成功发送的部分。
            repo.save_text_message(
                delivery_id=delivery_id,
                chat_id=result.chat_id,
                message_thread_id=result.message_thread_id,
                message_id=result.message_id,
            )
            conn.commit()
            progress(
                log_event(
                    "delivery",
                    "text_sent",
                    progress=item_progress,
                    target=candidate.target_key,
                    **candidate_log_fields(candidate),
                )
            )

        def save_pdf_result(result: TelegramSendResult) -> None:
            # 文件发送成功后立即保存 message_id，降低重复投递概率。
            repo.save_pdf_message(
                delivery_id=delivery_id,
                chat_id=result.chat_id,
                message_thread_id=result.message_thread_id,
                message_id=result.message_id,
            )
            conn.commit()
            progress(
                log_event(
                    "delivery",
                    "pdf_sent",
                    progress=item_progress,
                    target=candidate.target_key,
                    **candidate_log_fields(candidate),
                )
            )

        send_telegram_delivery(
            payload,
            pdf_path=pdf_path,
            send_text=candidate.text_message_id is None,
            send_pdf=candidate.pdf_message_id is None,
            config=runtime_config,
            on_text_sent=save_text_result,
            on_pdf_sent=save_pdf_result,
        )
        repo.mark_delivery_completed(delivery_id=delivery_id)
        conn.commit()
        progress(
            log_event(
                "delivery",
                "completed",
                progress=item_progress,
                target=candidate.target_key,
                **candidate_log_fields(candidate),
            )
        )
        return "completed"
    except TelegramSendOutcomeUnknown as exc:
        conn.rollback()
        # 这里的 unknown 表示外部结果不可确认，不能简单当作 failed 自动重发。
        repo.save_delivery_failure(
            delivery_id=delivery_id,
            status="unknown",
            failure_reason=str(exc),
            failure_log=traceback.format_exc(),
        )
        conn.commit()
        progress(
            log_event(
                "delivery",
                "unknown",
                level="WARNING",
                progress=item_progress,
                target=candidate.target_key,
                error=short_error(exc),
                **candidate_log_fields(candidate),
            )
        )
        return "unknown"
    except Exception as exc:
        conn.rollback()
        repo.save_delivery_failure(
            delivery_id=delivery_id,
            status="failed",
            failure_reason=str(exc),
            failure_log=traceback.format_exc(),
        )
        conn.commit()
        progress(
            log_event(
                "delivery",
                "failed",
                level="ERROR",
                progress=item_progress,
                target=candidate.target_key,
                error=short_error(exc),
                **candidate_log_fields(candidate),
            )
        )
        return "failed"


def _build_telegram_payload(candidate: WorkflowCandidate) -> TelegramSummaryPayload:
    """把数据库候选转换为 Telegram 发送载荷，并校验摘要字段可用。"""
    stored_summary = candidate.stored_summary
    if stored_summary is None:
        raise ValueError(f"summary is unavailable: {candidate.announcement_id}")
    return TelegramSummaryPayload(
        source=candidate.source,
        announcement_id=candidate.announcement_id,
        market=candidate.market,
        stock_code=candidate.stock_code,
        stock_key=candidate.stock_key,
        company_name=candidate.company_name,
        announcement=candidate.announcement,
        summary=stored_summary,
        search_keyword=candidate.search_keyword,
    )


def _require_pdf_path(candidate: WorkflowCandidate) -> Path:
    """投递必须依赖摘要阶段保存的本地 PDF 路径。"""
    if candidate.pdf_local_path is None:
        raise ValueError(f"PDF path is unavailable: {candidate.announcement_id}")
    return candidate.pdf_local_path


def _require_delivery_id(candidate: WorkflowCandidate) -> int:
    """投递候选必须带有 telegram_deliveries 主键。"""
    if candidate.delivery_id is None:
        raise ValueError(f"delivery id is unavailable: {candidate.announcement_id}")
    return candidate.delivery_id
