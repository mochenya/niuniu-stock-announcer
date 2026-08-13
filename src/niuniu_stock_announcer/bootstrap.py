"""唯一 application composition root。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlalchemy import Engine

from niuniu_stock_announcer.announcements.document import AnnouncementDocumentService
from niuniu_stock_announcer.announcements.providers.cninfo.service import (
    CninfoAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.sse.service import (
    SseAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.szse.service import (
    SzseAnnouncementService,
)
from niuniu_stock_announcer.announcements.service import AnnouncementProviderService
from niuniu_stock_announcer.config.settings import AppSettings
from niuniu_stock_announcer.db.connection import (
    create_db_engine,
    create_session_factory,
)
from niuniu_stock_announcer.db.migration import get_current_revision, upgrade_database
from niuniu_stock_announcer.db.unit_of_work import create_uow_factory
from niuniu_stock_announcer.delivery.service import ChinaDeliveryMaterializer
from niuniu_stock_announcer.im.telegram.run_log import (
    RunLogNotification,
    RunLogStageStats,
    RunLogSyncStats,
    TelegramRunLogNotifier,
)
from niuniu_stock_announcer.im.telegram.sender import TelegramSender
from niuniu_stock_announcer.im.telegram.target import parse_telegram_topic_url
from niuniu_stock_announcer.pipelines.china.pipeline import ChinaPipeline
from niuniu_stock_announcer.pipelines.china.discovery.schema import SyncResult
from niuniu_stock_announcer.pipelines.china.profile import ChinaMarketProfile
from niuniu_stock_announcer.pipelines.china.provider_resolver import (
    ChinaProviderResolver,
)
from niuniu_stock_announcer.pipelines.china.schema import ChinaPlan
from niuniu_stock_announcer.pipelines.china.stages.delivery import (
    DeliveryStage,
    DeliveryStageResult,
)
from niuniu_stock_announcer.pipelines.china.stages.summary import (
    SummaryStage,
    SummaryStageResult,
)
from niuniu_stock_announcer.pipelines.china.stages.sync import SyncStage
from niuniu_stock_announcer.summary.agents.china import (
    ChinaAnnouncementAgent,
    SummaryLLMClient,
)
from niuniu_stock_announcer.summary.service import SummaryService


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """保存全局恢复命令实际推进的两个 Stage 结果。"""

    summary: SummaryStageResult
    delivery: DeliveryStageResult


@dataclass(slots=True)
class ApplicationRuntime:
    """持有一个命令生命周期内的短期资源并负责关闭。"""

    engine: Engine
    provider_services: tuple[AnnouncementProviderService, ...]
    summary_client: SummaryLLMClient | None = None

    def close(self) -> None:
        """按外部 client、再数据库 Engine 的顺序释放资源。"""
        if self.summary_client is not None:
            self.summary_client.close()
        for service in self.provider_services:
            service.close()
        self.engine.dispose()

    def __enter__(self) -> ApplicationRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """保存设置并按命令构造完整 v2 application。"""

    settings: AppSettings

    @contextmanager
    def open_pipeline(
        self, plan: ChinaPlan, *, include_postprocess: bool = True
    ) -> Iterator[ChinaPipeline]:
        """构造一个绑定单一 Plan 的 China Pipeline。

        Args:
            plan: 已通过 Plan loader 校验的唯一 Plan。
            include_postprocess: 是否构造摘要、LLM 与 Telegram Stage；仅 `run` 需要。

        Yields:
            可执行 `sync`/`run` 的 China Pipeline。

        Raises:
            ValueError: 数据库、Telegram 或其他基础设施配置缺失。
        """
        runtime, pipeline = _build_pipeline(
            self.settings,
            plan,
            include_postprocess=include_postprocess,
        )
        try:
            yield pipeline
        finally:
            runtime.close()

    @contextmanager
    def open_recovery(
        self, *, require_summary: bool, require_delivery: bool
    ) -> Iterator[RecoveryApplication]:
        """构造不加载 Plan、不查询 Provider 的全局恢复 application。

        Yields:
            可执行 pending/failed recovery 的应用对象。
        """
        runtime, application = _build_recovery(
            self.settings,
            require_summary=require_summary,
            require_delivery=require_delivery,
        )
        try:
            yield application
        finally:
            runtime.close()

    def upgrade_database(self) -> None:
        """在显式数据库命令中运行 Alembic upgrade。"""
        engine = create_db_engine(self.settings.require_database_url())
        try:
            upgrade_database(engine)
        finally:
            engine.dispose()

    def current_database_revision(self) -> str | None:
        """读取数据库 revision，不隐式创建或升级 schema。"""
        engine = create_db_engine(self.settings.require_database_url())
        try:
            return get_current_revision(engine)
        finally:
            engine.dispose()

    def report_run(
        self,
        *,
        command: str,
        started_at: datetime,
        finished_at: datetime,
        sync: SyncResult | None = None,
        summary: SummaryStageResult | None = None,
        delivery: DeliveryStageResult | None = None,
        error: str | None = None,
    ) -> bool:
        """发送可选运行日志，不影响主命令结果。

        Args:
            command: 机器可读的 CLI 命令文本，不包含 secret。
            started_at: 命令开始时间，必须带时区。
            finished_at: 命令结束时间，必须带时区。
            sync: 可选同步 Stage 统计。
            summary: 可选摘要 Stage 统计。
            delivery: 可选 Telegram Stage 统计。
            error: 主命令失败时的脱敏错误文本。

        Returns:
            notifier 成功发送时返回 `True`；未配置 target 或通知失败时返回 `False`。
        """
        target = self.settings.telegram_run_log_target
        if not target:
            return False
        try:
            notifier = TelegramRunLogNotifier(
                bot_token=self.settings.require_telegram_bot_token(),
                target=target,
                timeout=self.settings.telegram_timeout,
                attach_file=self.settings.telegram_run_log_attach_file,
            )
            return notifier.notify(
                RunLogNotification(
                    command=command,
                    status=_run_report_status(sync, summary, delivery, error),
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=max(
                        0.0, (finished_at - started_at).total_seconds()
                    ),
                    sync=None if sync is None else _run_log_sync(sync),
                    summary=None if summary is None else _run_log_stage(summary),
                    delivery=None
                    if delivery is None
                    else _run_log_stage(delivery),
                    error=error,
                )
            )
        except Exception:
            # 运行日志是附加诊断能力；它不能把已完成的公告处理改写成失败。
            return False


class RecoveryApplication:
    """拥有共享 recovery Stage 的全局恢复入口。"""

    def __init__(
        self,
        *,
        summary_stage: SummaryStage,
        delivery_stage: DeliveryStage,
        settings: AppSettings,
    ) -> None:
        """绑定已组装的 Stage 与 stale 超时设置。"""
        self._summary_stage = summary_stage
        self._delivery_stage = delivery_stage
        self._settings = settings

    def process_pending(self, *, limit: int | None = None) -> RecoveryResult:
        """只处理 pending 摘要和 pending Telegram child。"""
        summary_stage = self._require_summary()
        delivery_stage = self._require_delivery()
        self._recover_stale(summary=True, delivery=True)
        summary = summary_stage.execute(mode="pending", limit=limit)
        delivery = delivery_stage.execute(mode="pending", limit=limit)
        return RecoveryResult(summary=summary, delivery=delivery)

    def retry_failed_summary(self, *, limit: int | None = None) -> SummaryStageResult:
        """只领取 failed 摘要，unknown 和 pending 均不触碰。"""
        summary_stage = self._require_summary()
        self._recover_stale(summary=True, delivery=False)
        return summary_stage.execute(mode="failed", limit=limit)

    def retry_failed_telegram(self, *, limit: int | None = None) -> DeliveryStageResult:
        """只领取 failed Telegram child，绝不重渲染或自动重发 unknown。"""
        delivery_stage = self._require_delivery()
        self._recover_stale(summary=False, delivery=True)
        return delivery_stage.execute(mode="failed", limit=limit)

    def retry_failed_all(self, *, limit: int | None = None) -> RecoveryResult:
        """先重试摘要，再处理 failed 与新产生的 pending Telegram child。"""
        summary_stage = self._require_summary()
        delivery_stage = self._require_delivery()
        self._recover_stale(summary=True, delivery=True)
        summary = summary_stage.execute(mode="failed", limit=limit)
        delivery_stage.execute(mode="failed", limit=limit)
        delivery = delivery_stage.execute(mode="pending", limit=limit)
        return RecoveryResult(summary=summary, delivery=delivery)

    def _recover_stale(self, *, summary: bool, delivery: bool) -> None:
        now = datetime.now(UTC)
        if summary:
            self._summary_stage.recover_stale(
                started_before=now
                - timedelta(minutes=self._settings.summary_running_timeout_minutes)
            )
        if delivery:
            self._delivery_stage.recover_stale(
                started_before=now
                - timedelta(minutes=self._settings.telegram_running_timeout_minutes)
            )

    def _require_summary(self) -> SummaryStage:
        if self._summary_stage is None:
            raise RuntimeError("当前 recovery application 未构造摘要 Stage")
        return self._summary_stage

    def _require_delivery(self) -> DeliveryStage:
        if self._delivery_stage is None:
            raise RuntimeError("当前 recovery application 未构造 Telegram Stage")
        return self._delivery_stage


def bootstrap(settings: AppSettings) -> ApplicationContext:
    """构造不连接外部系统的 application context。

    Args:
        settings: 已完成环境与 `.env` 覆盖解析的应用设置。

    Returns:
        可由 CLI 按命令打开 Pipeline、Recovery 或 database application 的上下文。
    """
    return ApplicationContext(settings=settings)


def _build_pipeline(
    settings: AppSettings,
    plan: ChinaPlan,
    *,
    include_postprocess: bool,
) -> tuple[ApplicationRuntime, ChinaPipeline]:
    runtime, components = _build_components(
        settings,
        include_discovery=True,
        include_summary=include_postprocess,
    )
    try:
        provider_services = components["provider_services"]
        resolver = ChinaProviderResolver(plan.announcement_providers, provider_services)
        materializer = ChinaDeliveryMaterializer()
        uow_factory = components["uow_factory"]
        sync_stage = SyncStage(
            resolver,
            uow_factory,
            parse_telegram_topic_url,
            materializer,
        )
        summary_stage = None
        delivery_stage = None
        if include_postprocess:
            summary_stage = _create_summary_stage(
                settings, components, materializer, runtime
            )
            delivery_stage = (
                _create_delivery_stage(settings, components)
                if _plan_has_delivery_target(plan)
                else None
            )
        pipeline = ChinaPipeline(
            plan,
            profile=ChinaMarketProfile(),
            sync_stage=sync_stage,
            summary_stage=summary_stage,
            delivery_stage=delivery_stage,
        )
    except BaseException:
        runtime.close()
        raise
    return runtime, pipeline


def _build_recovery(
    settings: AppSettings,
    *,
    require_summary: bool,
    require_delivery: bool,
) -> tuple[ApplicationRuntime, RecoveryApplication]:
    runtime, components = _build_components(
        settings,
        include_discovery=False,
        include_summary=require_summary,
    )
    try:
        materializer = ChinaDeliveryMaterializer()
        summary_stage = (
            _create_summary_stage(settings, components, materializer, runtime)
            if require_summary
            else None
        )
        delivery_stage = (
            _create_delivery_stage(settings, components) if require_delivery else None
        )
    except BaseException:
        runtime.close()
        raise
    return runtime, RecoveryApplication(
        summary_stage=summary_stage,
        delivery_stage=delivery_stage,
        settings=settings,
    )


def _build_components(
    settings: AppSettings,
    *,
    include_discovery: bool,
    include_summary: bool,
) -> tuple[ApplicationRuntime, dict[str, object]]:
    engine = create_db_engine(settings.require_database_url())
    session_factory = create_session_factory(engine)
    uow_factory = create_uow_factory(session_factory)
    provider_services = (
        _create_provider_services() if include_discovery or include_summary else {}
    )
    runtime = ApplicationRuntime(
        engine=engine,
        provider_services=tuple(provider_services.values()),
    )
    return runtime, {
        "uow_factory": uow_factory,
        "provider_services": provider_services,
    }


def _create_provider_services() -> dict[str, AnnouncementProviderService]:
    return {
        "cninfo": CninfoAnnouncementService(),
        "sse": SseAnnouncementService(),
        "szse": SzseAnnouncementService(),
    }


def _create_summary_stage(
    settings: AppSettings,
    components: Mapping[str, object],
    materializer: ChinaDeliveryMaterializer,
    runtime: ApplicationRuntime,
) -> SummaryStage:
    provider_services = components["provider_services"]
    if not isinstance(provider_services, dict):
        raise TypeError("provider service registry 类型不正确")
    document_service = AnnouncementDocumentService(
        settings.document_storage_root,
        provider_services,
    )
    client = SummaryLLMClient(settings=settings)
    runtime.summary_client = client
    agent = ChinaAnnouncementAgent(client)
    summary_service = SummaryService(document_service, agent)
    return SummaryStage(
        summary_service,
        components["uow_factory"],
        materializer,
        max_failures=settings.summary_max_failures,
    )


def _create_delivery_stage(
    settings: AppSettings,
    components: Mapping[str, object],
) -> DeliveryStage:
    sender = TelegramSender(
        bot_token=settings.require_telegram_bot_token(),
        timeout=settings.telegram_timeout,
        document_storage_root=settings.document_storage_root,
    )
    return DeliveryStage(sender, components["uow_factory"])


def _plan_has_delivery_target(plan: ChinaPlan) -> bool:
    return any(
        scope.delivery.telegram is not None for scope in plan.market_scopes.values()
    )


def _run_log_sync(result: SyncResult) -> RunLogSyncStats:
    return RunLogSyncStats(
        fetched=result.persisted_items,
        filtered=result.filtered_matches,
        seeded=result.selected_matches,
        errors=len(result.errors),
        new_refs=len(result.activations),
    )


def _run_log_stage(result: object) -> RunLogStageStats:
    return RunLogStageStats(
        completed=getattr(result, "completed_count", 0)
        or getattr(result, "sent_count", 0),
        failed=getattr(result, "failed_count", 0),
        unknown=getattr(result, "unknown_count", 0),
    )


def _run_report_status(
    sync: SyncResult | None,
    summary: SummaryStageResult | None,
    delivery: DeliveryStageResult | None,
    error: str | None,
) -> str:
    if error:
        return "failed"
    if sync is not None and sync.errors:
        return "warning"
    if summary is not None and summary.failed_count:
        return "warning"
    if delivery is not None and (
        delivery.failed_count or delivery.unknown_count
    ):
        return "warning"
    return "success"
