from __future__ import annotations

import traceback
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from random import uniform
from time import sleep

from announcements.download import (
    AnnouncementPdfClient,
    download_announcement_pdf,
)
from announcements.sources import create_announcement_client
from db.repository import AnnouncementRepository
from domain.config_models import RuntimeConfig
from domain.common import AnnouncementSource
from domain.summary_models import PdfSummaryRequest
from domain.workflow_models import (
    PipelineStageSummary,
    WorkflowCandidate,
)
from log.events import log_event
from summary.client import SummaryLLMClient
from summary.errors import serialize_summary_error
from summary.service import summarize_pdf
from workflow.common import (
    ProgressReporter,
    candidate_log_fields,
    format_progress,
    noop_progress,
    require_text,
    short_error,
)


def run_summary_candidates(
    repo: AnnouncementRepository,
    *,
    conn,
    candidates: Sequence[WorkflowCandidate],
    runtime_config: RuntimeConfig,
    progress: ProgressReporter | None = None,
) -> PipelineStageSummary:
    """依次处理摘要候选，并复用同一个 LLM 客户端连接。

    调用方负责提供已筛好的候选集合；本函数只负责阶段执行和结果计数。
    """
    report = progress or noop_progress
    result = PipelineStageSummary(candidate_count=len(candidates))
    report(log_event("summary", "running", total=len(candidates)))
    with (
        ExitStack() as source_client_stack,
        SummaryLLMClient(config=runtime_config) as llm_client,
    ):
        source_clients: dict[AnnouncementSource, AnnouncementPdfClient] = {}
        for index, candidate in enumerate(candidates, start=1):
            if _run_summary_candidate(
                repo,
                conn=conn,
                candidate=candidate,
                runtime_config=runtime_config,
                llm_client=llm_client,
                source_client_stack=source_client_stack,
                source_clients=source_clients,
                progress=report,
                index=index,
                total=len(candidates),
            ):
                result.completed_count += 1
            else:
                result.failed_count += 1
    report(
        log_event(
            "summary",
            "finished",
            completed=result.completed_count,
            failed=result.failed_count,
        )
    )
    return result


def _run_summary_candidate(
    repo: AnnouncementRepository,
    *,
    conn,
    candidate: WorkflowCandidate,
    runtime_config: RuntimeConfig,
    llm_client: SummaryLLMClient,
    source_client_stack: ExitStack,
    source_clients: dict[AnnouncementSource, AnnouncementPdfClient],
    progress: ProgressReporter,
    index: int,
    total: int,
) -> bool:
    """处理单条摘要候选。

    running 状态会先提交，避免长时间下载 PDF 或调用 LLM 时被其他进程重复领取。
    后续成功或失败也会独立提交，保证 retry 命令能看到最新状态。
    """
    item_progress = format_progress(index, total)
    progress(
        log_event(
            "summary",
            "processing",
            progress=item_progress,
            **candidate_log_fields(candidate),
        )
    )
    pdf_path: Path | None = None
    try:
        repo.mark_summary_running(
            source=candidate.source,
            announcement_id=candidate.announcement_id,
        )
        # 先提交 running 状态，避免长时间下载或 LLM 请求期间候选被重复领取。
        conn.commit()
        if candidate.pdf_local_path is not None and candidate.pdf_local_path.is_file():
            pdf_path = candidate.pdf_local_path
        else:
            progress(
                log_event(
                    "summary",
                    "pdf_downloading",
                    progress=item_progress,
                    **candidate_log_fields(candidate),
                )
            )
            _wait_before_pdf_download(runtime_config.pdf_download_delay_seconds)
            pdf_path = download_announcement_pdf(
                candidate.announcement,
                save_dir=runtime_config.pdf_save_dir,
                client=_get_source_client(
                    candidate.source,
                    source_client_stack=source_client_stack,
                    source_clients=source_clients,
                ),
            )
        progress(
            log_event(
                "summary",
                "llm_requesting",
                progress=item_progress,
                **candidate_log_fields(candidate),
            )
        )
        summary_result = summarize_pdf(
            PdfSummaryRequest(
                announcement_id=candidate.announcement_id,
                pdf_path=pdf_path,
                company_name=candidate.company_name,
                announcement_title=require_text(
                    candidate.announcement.announcement_title,
                    "announcement_title",
                ),
            ),
            config=runtime_config,
            llm_client=llm_client,
        )
        repo.save_summary_success(
            source=candidate.source,
            announcement_id=candidate.announcement_id,
            result=summary_result,
            pdf_local_path=pdf_path,
        )
        conn.commit()
        progress(
            log_event(
                "summary",
                "completed",
                progress=item_progress,
                tokens=_format_tokens(
                    summary_result.input_tokens,
                    summary_result.output_tokens,
                ),
                pdf=pdf_path,
                **candidate_log_fields(candidate),
            )
        )
        return True
    except Exception as exc:
        conn.rollback()
        # 失败也要落库并提交，后续 retry 命令依赖 failed 状态和 failure_log。
        repo.save_summary_failure(
            source=candidate.source,
            announcement_id=candidate.announcement_id,
            failure_reason=serialize_summary_error(exc),
            failure_log=traceback.format_exc(),
            pdf_local_path=pdf_path,
        )
        conn.commit()
        progress(
            log_event(
                "summary",
                "failed",
                level="ERROR",
                progress=item_progress,
                error=short_error(exc),
                **candidate_log_fields(candidate),
            )
        )
        return False


def _wait_before_pdf_download(delay_seconds: tuple[float, float]) -> None:
    min_delay, max_delay = delay_seconds
    delay = uniform(min_delay, max_delay)
    if delay <= 0:
        return
    sleep(delay)


def _get_source_client(
    source: AnnouncementSource,
    *,
    source_client_stack: ExitStack,
    source_clients: dict[AnnouncementSource, AnnouncementPdfClient],
) -> AnnouncementPdfClient:
    client = source_clients.get(source)
    if client is None:
        client = source_client_stack.enter_context(create_announcement_client(source))
        source_clients[source] = client
    return client


def _format_tokens(input_tokens: int | None, output_tokens: int | None) -> str:
    if input_tokens is None and output_tokens is None:
        return "-"
    return f"{input_tokens or 0}/{output_tokens or 0}"
