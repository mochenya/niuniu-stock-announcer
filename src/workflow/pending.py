from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.runtime import load_runtime_config
from db.connection import connect_database
from db.repository import AnnouncementRepository
from db.schema import ensure_schema
from domain.config_models import RuntimeConfig
from domain.workflow_models import (
    AnnouncementRef,
    PipelineStageSummary,
)
from workflow.common import (
    ProgressReporter,
    dedupe_candidates,
    dedupe_refs,
    require_database_url,
)
from workflow.delivery_stage import run_delivery_candidates
from workflow.summary_stage import run_summary_candidates


@dataclass(frozen=True)
class _WorkflowResources:
    runtime_config: RuntimeConfig
    conn: Any
    repo: AnnouncementRepository


@contextmanager
def _open_workflow_resources(
    *,
    env_file: str | Path | None,
    require_llm: bool = False,
    require_telegram: bool = False,
) -> Iterator[_WorkflowResources]:
    runtime_config = load_runtime_config(
        env_file=env_file,
        require_database=True,
        require_llm=require_llm,
        require_telegram=require_telegram,
    )
    with connect_database(require_database_url(runtime_config)) as conn:
        ensure_schema(conn)
        yield _WorkflowResources(
            runtime_config=runtime_config,
            conn=conn,
            repo=AnnouncementRepository(conn),
        )


def run_new_workflow(
    *,
    refs: Sequence[AnnouncementRef],
    env_file: str | Path | None = None,
    limit: int | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[PipelineStageSummary, PipelineStageSummary]:
    """处理本轮同步新增的工作流记录。

    refs 来自 sync_once 返回的 new_refs；摘要阶段只领取这些公告的 pending 记录。
    """
    report = progress
    deduped_refs = dedupe_refs(refs)
    with _open_workflow_resources(
        env_file=env_file,
        require_llm=True,
        require_telegram=True,
    ) as resources:
        summary_candidates = resources.repo.list_summary_candidates(
            refs=deduped_refs,
            statuses=("pending",),
            limit=limit,
        )
        summary_result = run_summary_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=summary_candidates,
            runtime_config=resources.runtime_config,
            progress=report,
        )
        delivery_candidates = resources.repo.list_delivery_candidates(
            refs=deduped_refs,
            statuses=("pending",),
            limit=limit,
        )
        delivery_result = run_delivery_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=delivery_candidates,
            runtime_config=resources.runtime_config,
            progress=report,
        )
    return summary_result, delivery_result


def process_pending(
    *,
    env_file: str | Path | None = None,
    limit: int | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[PipelineStageSummary, PipelineStageSummary]:
    """处理库里遗留的 pending 摘要和投递记录。

    该入口用于单独补跑，不执行公告同步，也不处理 failed/unknown 状态。
    """
    report = progress
    with _open_workflow_resources(
        env_file=env_file,
        require_llm=True,
        require_telegram=True,
    ) as resources:
        summary_candidates = resources.repo.list_summary_candidates(
            statuses=("pending",),
            limit=limit,
        )
        summary_result = run_summary_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=summary_candidates,
            runtime_config=resources.runtime_config,
            progress=report,
        )
        delivery_candidates = resources.repo.list_delivery_candidates(
            statuses=("pending",),
            limit=limit,
        )
        delivery_result = run_delivery_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=delivery_candidates,
            runtime_config=resources.runtime_config,
            progress=report,
        )
    return summary_result, delivery_result


def retry_failed_summaries(
    *,
    env_file: str | Path | None = None,
    limit: int | None = None,
    progress: ProgressReporter | None = None,
) -> PipelineStageSummary:
    """只重试 failed 摘要记录。

    成功后不会在这里继续投递；需要投递时使用 process-pending 或 retry-failed all。
    """
    with _open_workflow_resources(
        env_file=env_file,
        require_llm=True,
    ) as resources:
        candidates = resources.repo.list_summary_candidates(
            statuses=("failed",),
            limit=limit,
        )
        return run_summary_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=candidates,
            runtime_config=resources.runtime_config,
            progress=progress,
        )


def retry_failed_deliveries(
    *,
    env_file: str | Path | None = None,
    limit: int | None = None,
    progress: ProgressReporter | None = None,
) -> PipelineStageSummary:
    """只重试 failed 投递记录。

    unknown 状态表示外部发送结果不可确认，不能由该入口自动重发。
    """
    with _open_workflow_resources(
        env_file=env_file,
        require_telegram=True,
    ) as resources:
        candidates = resources.repo.list_delivery_candidates(
            statuses=("failed",),
            limit=limit,
        )
        return run_delivery_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=candidates,
            runtime_config=resources.runtime_config,
            progress=progress,
        )


def retry_failed_all(
    *,
    env_file: str | Path | None = None,
    limit: int | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[PipelineStageSummary, PipelineStageSummary]:
    """依次重试 failed 摘要和 failed 投递。

    摘要重试成功后，会补充领取对应公告的 pending/failed 投递候选。
    """
    with _open_workflow_resources(
        env_file=env_file,
        require_llm=True,
        require_telegram=True,
    ) as resources:
        summary_candidates = resources.repo.list_summary_candidates(
            statuses=("failed",),
            limit=limit,
        )
        summary_result = run_summary_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=summary_candidates,
            runtime_config=resources.runtime_config,
            progress=progress,
        )
        successful_refs = [candidate.ref for candidate in summary_candidates]
        delivery_candidates = resources.repo.list_delivery_candidates(
            statuses=("failed",),
            limit=limit,
        )
        delivery_candidates.extend(
            resources.repo.list_delivery_candidates(
                refs=successful_refs,
                statuses=("pending", "failed"),
            )
        )
        delivery_candidates = dedupe_candidates(delivery_candidates)
        delivery_result = run_delivery_candidates(
            resources.repo,
            conn=resources.conn,
            candidates=delivery_candidates,
            runtime_config=resources.runtime_config,
            progress=progress,
        )
    return summary_result, delivery_result
