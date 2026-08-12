"""China 公告 discovery、过滤和短事务持久化阶段。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from niuniu_stock_announcer.announcements.schema import (
    ChinaAnnouncement,
    CninfoSourceSnapshot,
    ProviderAnnouncement,
    ProviderKey,
    SseSourceSnapshot,
    SzseSourceSnapshot,
)
from niuniu_stock_announcer.db.schema import (
    ChinaAnnouncementWrite,
    ChinaMatchWrite,
    CninfoAnnouncementWrite,
    SseAnnouncementWrite,
    SzseAnnouncementWrite,
    TelegramDeliveryWrite,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.filters.title import evaluate_title_filter
from niuniu_stock_announcer.im.telegram.schema import TelegramTarget
from niuniu_stock_announcer.pipelines.china.discovery.schema import (
    DiscoveryCandidate,
    DiscoveryQueryTask,
    SyncActivation,
    SyncError,
    SyncErrorPhase,
    SyncResult,
)
from niuniu_stock_announcer.pipelines.china.provider_resolver import (
    ChinaProviderResolver,
)
from niuniu_stock_announcer.pipelines.china.schema import TelegramTargetPlan

UnitOfWorkFactory = Callable[[], AbstractContextManager[UnitOfWork]]
TelegramTargetParser = Callable[[str], TelegramTarget]
TerminalDeliveryMaterializer = Callable[[UnitOfWork, int, int], None]


@dataclass(slots=True)
class _MutableSyncResult:
    queries_succeeded: int = 0
    persisted_items: int = 0
    created_matches: int = 0
    repeated_matches: int = 0
    selected_matches: int = 0
    filtered_matches: int = 0
    activations: list[SyncActivation] = field(default_factory=list)
    errors: list[SyncError] = field(default_factory=list)

    def freeze(self) -> SyncResult:
        return SyncResult(
            queries_succeeded=self.queries_succeeded,
            persisted_items=self.persisted_items,
            created_matches=self.created_matches,
            repeated_matches=self.repeated_matches,
            selected_matches=self.selected_matches,
            filtered_matches=self.filtered_matches,
            activations=tuple(self.activations),
            errors=tuple(self.errors),
        )


@dataclass(frozen=True, slots=True)
class _PersistOutcome:
    created_match: bool
    selected: bool
    activation: SyncActivation | None


class SyncStage:
    """在 Provider 网络边界外按公告逐条提交 discovery 结果。"""

    def __init__(
        self,
        provider_resolver: ChinaProviderResolver,
        uow_factory: UnitOfWorkFactory,
        target_parser: TelegramTargetParser,
        terminal_delivery_materializer: TerminalDeliveryMaterializer,
    ) -> None:
        """绑定显式 Provider resolver、UoW factory 和 target parser。

        Args:
            provider_resolver: 只按 Plan route 选择已注入 Provider 的 resolver。
            uow_factory: 每次调用返回全新短事务的 factory。
            target_parser: 把 Plan target URL 转换为 Bot API chat/thread ID 的纯函数。
            terminal_delivery_materializer: 在统一 summary 行锁内物化终态 child 的能力。
        """
        self._provider_resolver = provider_resolver
        self._uow_factory = uow_factory
        self._target_parser = target_parser
        self._terminal_delivery_materializer = terminal_delivery_materializer

    def execute(self, tasks: Sequence[DiscoveryQueryTask]) -> SyncResult:
        """执行查询、聚合候选并逐条持久化，隔离查询和公告失败。

        Args:
            tasks: 由单一 discovery strategy 编译的 typed 查询任务。

        Returns:
            只包含提交成功统计、本轮新 work activation 和受控错误的结果。
        """
        _validate_task_batch(tasks)
        resolved_targets = {
            task.target.target_url: self._target_parser(task.target.target_url)
            for task in tasks
            if task.target is not None
        }
        result = _MutableSyncResult()
        candidates: dict[tuple[ProviderKey, str], DiscoveryCandidate] = {}
        conflicted_identities: set[tuple[ProviderKey, str]] = set()
        for task in tasks:
            try:
                provider = self._provider_resolver.resolve(task.query.exchange)
                if provider.provider_key != task.provider_key:
                    raise ValueError("task Provider 与 Plan resolver 结果不一致")
            except Exception as exc:
                self._append_error(result, task, phase="resolve", error=exc)
                continue
            try:
                # Query 阶段不创建 UoW；SDK 内部分页和节流 sleep 也不会持有 Session。
                query_result = provider.query(task.query)
                if query_result.provider_key != task.provider_key:
                    raise ValueError("Provider query result 身份与 task 不一致")
            except Exception as exc:
                self._append_error(result, task, phase="query", error=exc)
                continue
            result.queries_succeeded += 1
            for item_error in query_result.item_errors:
                self._append_error(
                    result,
                    task,
                    phase="map",
                    error=ValueError(item_error.message),
                )
            for provider_item in query_result.items:
                identity = (
                    provider_item.announcement.provider_key,
                    provider_item.announcement.provider_announcement_id,
                )
                try:
                    candidate = self._build_candidate(task, provider_item)
                    if identity in conflicted_identities:
                        continue
                    candidates[identity] = self._merge_candidate(
                        candidates.get(identity), candidate
                    )
                except Exception as exc:
                    candidates.pop(identity, None)
                    conflicted_identities.add(identity)
                    self._append_error(
                        result,
                        task,
                        phase="map",
                        error=exc,
                        provider_announcement_id=(
                            provider_item.announcement.provider_announcement_id
                        ),
                    )

        for candidate in candidates.values():
            try:
                target = candidate.task.target
                resolved_target = (
                    None if target is None else resolved_targets[target.target_url]
                )
                outcome = self._persist_candidate(candidate, resolved_target)
            except Exception as exc:
                self._append_error(
                    result,
                    candidate.task,
                    phase="persist",
                    error=exc,
                    provider_announcement_id=(
                        candidate.provider_item.announcement.provider_announcement_id
                    ),
                )
                continue
            # `_persist_candidate` 返回时 context manager 已成功 commit；提交失败不会污染统计。
            result.persisted_items += 1
            if outcome.created_match:
                result.created_matches += 1
            else:
                result.repeated_matches += 1
            if outcome.selected:
                result.selected_matches += 1
            else:
                result.filtered_matches += 1
            if outcome.activation is not None:
                result.activations.append(outcome.activation)
        return result.freeze()

    def _build_candidate(
        self,
        task: DiscoveryQueryTask,
        provider_item: ProviderAnnouncement,
    ) -> DiscoveryCandidate:
        keyword = task.query.search_keyword
        return DiscoveryCandidate(
            task=task,
            provider_item=provider_item,
            filter_decision=evaluate_title_filter(
                provider_item.announcement.title,
                task.title_exclude_keywords,
            ),
            matched_search_keywords=() if keyword is None else (keyword,),
        )

    def _merge_candidate(
        self,
        existing: DiscoveryCandidate | None,
        current: DiscoveryCandidate,
    ) -> DiscoveryCandidate:
        if existing is None:
            return current
        if existing.task.plan_key != current.task.plan_key:
            raise ValueError("一次 SyncStage 不能混合多个 Plan")
        if existing.task.discovery_type != current.task.discovery_type:
            raise ValueError("同一公告不能混合两种 discovery 类型")
        if existing.task.market_scope != current.task.market_scope:
            raise ValueError("同一 Provider 公告在本轮出现冲突 market scope")
        if existing.task.target != current.task.target:
            raise ValueError("同一 scope 公告出现冲突 delivery target")
        if existing.provider_item != current.provider_item:
            raise ValueError("同一 Provider 身份在本轮出现冲突公告事实")
        if existing.filter_decision != current.filter_decision:
            raise ValueError("同一 scope 公告出现冲突过滤证据")
        if existing.task.discovery_type == "selected_stocks":
            # 同一公告可能由 Provider 的多证券数组或多只配置股票命中；公告/match 仍只
            # 创建一次，首次 Plan 顺序对应的查询证据作为冻结审计事实。
            return existing.model_copy(
                update={"hit_increment": existing.hit_increment + current.hit_increment}
            )
        keywords = tuple(
            dict.fromkeys(
                [
                    *existing.matched_search_keywords,
                    *current.matched_search_keywords,
                ]
            )
        )
        return existing.model_copy(
            update={
                "matched_search_keywords": keywords,
                "hit_increment": existing.hit_increment + current.hit_increment,
            }
        )

    def _persist_candidate(
        self,
        candidate: DiscoveryCandidate,
        resolved_target: TelegramTarget | None,
    ) -> _PersistOutcome:
        with self._uow_factory() as uow:
            announcement = uow.china_announcements.upsert(
                _announcement_write(candidate.provider_item.announcement)
            )
            _upsert_source_snapshot(uow, announcement.id, candidate.provider_item)
            match_result = uow.china_matches.record(
                _match_write(candidate, announcement.id),
                hit_increment=candidate.hit_increment,
            )
            selected = match_result.record.filter_status == "selected"
            activation = None
            if match_result.created and selected:
                summary = uow.china_summaries.ensure(announcement.id)
                delivery_id = None
                if candidate.task.target is not None:
                    if resolved_target is None:
                        raise ValueError("已配置 target 但缺少预解析结果")
                    # 新 delivery 与 summary terminal 保存共用统一行锁顺序；若 summary
                    # 已终态，必须在当前事务物化 child，避免永久停留 pending。
                    locked = uow.china_summaries.lock(summary.id)
                    delivery = uow.telegram.ensure_delivery(
                        _delivery_write(
                            candidate.task.target,
                            resolved_target,
                            summary_id=summary.id,
                            plan_key=candidate.task.plan_key,
                            market_scope=candidate.task.market_scope,
                        )
                    )
                    delivery_id = delivery.id
                    if locked.status in {"completed", "skipped"}:
                        self._terminal_delivery_materializer(
                            uow, summary.id, delivery.id
                        )
                activation = SyncActivation(
                    announcement_id=announcement.id,
                    match_id=match_result.record.id,
                    summary_id=summary.id,
                    delivery_id=delivery_id,
                )
            return _PersistOutcome(
                created_match=match_result.created,
                selected=selected,
                activation=activation,
            )

    @staticmethod
    def _append_error(
        result: _MutableSyncResult,
        task: DiscoveryQueryTask,
        *,
        phase: SyncErrorPhase,
        error: Exception,
        provider_announcement_id: str | None = None,
    ) -> None:
        result.errors.append(
            SyncError(
                phase=phase,
                provider_key=task.provider_key,
                exchange=task.query.exchange,
                stock_code=task.query.stock_code,
                search_keyword=task.query.search_keyword,
                provider_announcement_id=provider_announcement_id,
                error_type=error.__class__.__name__,
                message=_short_error(error),
            )
        )


def _announcement_write(value: ChinaAnnouncement) -> ChinaAnnouncementWrite:
    return ChinaAnnouncementWrite(
        provider_key=value.provider_key,
        provider_announcement_id=value.provider_announcement_id,
        market_scope=value.market_scope,
        exchanges=tuple(item.exchange for item in value.securities),
        stock_codes=tuple(item.stock_code for item in value.securities),
        stock_names=tuple(item.stock_name for item in value.securities),
        title=value.title,
        published_at=value.published_at,
        source_url=value.source_url,
    )


def _validate_task_batch(tasks: Sequence[DiscoveryQueryTask]) -> None:
    plan_keys = {task.plan_key for task in tasks}
    discovery_types = {task.discovery_type for task in tasks}
    if len(plan_keys) > 1:
        raise ValueError("一次 SyncStage 只能执行一个 Plan")
    if len(discovery_types) > 1:
        raise ValueError("一次 SyncStage 只能执行一种 discovery strategy")


def _short_error(error: Exception, *, limit: int = 500) -> str:
    message = str(error)
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def _upsert_source_snapshot(
    uow: UnitOfWork,
    announcement_id: int,
    value: ProviderAnnouncement,
) -> None:
    snapshot = value.source_snapshot
    if isinstance(snapshot, CninfoSourceSnapshot):
        uow.cninfo_announcements.upsert(
            CninfoAnnouncementWrite(
                china_announcement_id=announcement_id,
                **snapshot.model_dump(exclude={"provider_key"}),
            )
        )
        return
    if isinstance(snapshot, SseSourceSnapshot):
        uow.sse_announcements.upsert(
            SseAnnouncementWrite(
                china_announcement_id=announcement_id,
                **snapshot.model_dump(exclude={"provider_key"}),
            )
        )
        return
    if isinstance(snapshot, SzseSourceSnapshot):
        uow.szse_announcements.upsert(
            SzseAnnouncementWrite(
                china_announcement_id=announcement_id,
                **snapshot.model_dump(exclude={"provider_key"}),
            )
        )
        return
    raise TypeError("不支持的 Provider source snapshot")


def _match_write(
    candidate: DiscoveryCandidate, announcement_id: int
) -> ChinaMatchWrite:
    task = candidate.task
    selected = candidate.filter_decision.outcome == "selected"
    return ChinaMatchWrite(
        china_announcement_id=announcement_id,
        plan_key=task.plan_key,
        discovery_type=task.discovery_type,
        market_scope=task.market_scope,
        query_exchange=(
            task.query.exchange if task.discovery_type == "selected_stocks" else None
        ),
        query_stock_code=(
            task.query.stock_code if task.discovery_type == "selected_stocks" else None
        ),
        query_provider_key=(
            task.provider_key if task.discovery_type == "selected_stocks" else None
        ),
        matched_search_keywords=candidate.matched_search_keywords,
        filter_status="selected" if selected else "filtered",
        filter_decisions=(candidate.filter_decision,),
    )


def _delivery_write(
    target: TelegramTargetPlan,
    resolved: TelegramTarget,
    *,
    summary_id: int,
    plan_key: str,
    market_scope: str,
) -> TelegramDeliveryWrite:
    return TelegramDeliveryWrite(
        producer_key="china_summary",
        business_key=str(summary_id),
        plan_key=plan_key,
        market_scope=market_scope,
        target_key=target.target_key,
        target_url=target.target_url,
        target_chat_id=resolved.chat_id,
        target_message_thread_id=resolved.message_thread_id,
        send_original_document=target.send_original_document,
    )
