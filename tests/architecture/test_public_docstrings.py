"""跨层公共边界的中文 Google docstring 架构检查。"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable

from niuniu_stock_announcer.announcements.document import (
    AnnouncementDocumentService,
)
from niuniu_stock_announcer.announcements.providers.cninfo import (
    CninfoAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.sse import (
    SseAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.szse import (
    SzseAnnouncementService,
)
from niuniu_stock_announcer.bootstrap import bootstrap
from niuniu_stock_announcer.config.env import (
    load_plan_environment,
    resolve_environment_references,
)
from niuniu_stock_announcer.config.plan_loader import load_china_plan
from niuniu_stock_announcer.config.settings import load_app_settings
from niuniu_stock_announcer.db.connection import (
    create_db_engine,
    create_session_factory,
)
from niuniu_stock_announcer.db.migration import (
    get_current_revision,
    upgrade_database,
)
from niuniu_stock_announcer.db.unit_of_work import create_uow_factory
from niuniu_stock_announcer.filters.title import evaluate_title_filter
from niuniu_stock_announcer.pipelines.china.discovery.market_keywords import (
    compile_market_keyword_tasks,
)
from niuniu_stock_announcer.pipelines.china.discovery.selected_stocks import (
    compile_selected_stock_tasks,
)
from niuniu_stock_announcer.pipelines.china.profile import ChinaMarketProfile
from niuniu_stock_announcer.pipelines.china.provider_resolver import (
    ChinaProviderResolver,
)
from niuniu_stock_announcer.pipelines.china.stages.sync import SyncStage
from niuniu_stock_announcer.storage.document import (
    resolve_storage_path,
    validate_storage_relative_path,
)

PUBLIC_BOUNDARIES: tuple[Callable[..., object], ...] = (
    bootstrap,
    load_app_settings,
    load_plan_environment,
    resolve_environment_references,
    load_china_plan,
    create_db_engine,
    create_session_factory,
    upgrade_database,
    get_current_revision,
    create_uow_factory,
    validate_storage_relative_path,
    resolve_storage_path,
    CninfoAnnouncementService.__init__,
    CninfoAnnouncementService.query,
    CninfoAnnouncementService.download_pdf,
    SseAnnouncementService.__init__,
    SseAnnouncementService.query,
    SseAnnouncementService.download_pdf,
    SzseAnnouncementService.__init__,
    SzseAnnouncementService.query,
    SzseAnnouncementService.download_pdf,
    AnnouncementDocumentService.__init__,
    AnnouncementDocumentService.ensure_pdf,
    evaluate_title_filter,
    ChinaMarketProfile.exchanges_for_scope,
    ChinaMarketProfile.scope_for_exchange,
    ChinaProviderResolver.__init__,
    ChinaProviderResolver.provider_key_for,
    ChinaProviderResolver.resolve,
    compile_selected_stock_tasks,
    compile_market_keyword_tasks,
    SyncStage.__init__,
    SyncStage.execute,
)


def test_registered_public_boundaries_use_chinese_google_docstrings() -> None:
    for boundary in PUBLIC_BOUNDARIES:
        docstring = inspect.getdoc(boundary) or ""
        assert re.search(r"[\u4e00-\u9fff]", docstring), boundary.__qualname__
        assert "Args:" in docstring, boundary.__qualname__
        for name, parameter in inspect.signature(boundary).parameters.items():
            if name in {"self", "cls"}:
                continue
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            assert re.search(rf"^\s*{re.escape(name)}:", docstring, re.MULTILINE), (
                boundary.__qualname__,
                name,
            )
