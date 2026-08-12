"""一个 Session、一个短事务的 UnitOfWork。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from niuniu_stock_announcer.db.repositories.china.announcements import (
    ChinaAnnouncementRepository,
)
from niuniu_stock_announcer.db.repositories.china.matches import (
    ChinaMatchRepository,
)
from niuniu_stock_announcer.db.repositories.china.providers.cninfo import (
    CninfoAnnouncementRepository,
)
from niuniu_stock_announcer.db.repositories.china.providers.sse import (
    SseAnnouncementRepository,
)
from niuniu_stock_announcer.db.repositories.china.providers.szse import (
    SzseAnnouncementRepository,
)
from niuniu_stock_announcer.db.repositories.china.summaries import (
    ChinaSummaryRepository,
)
from niuniu_stock_announcer.db.repositories.telegram import TelegramRepository


class UnitOfWork:
    """把一个短事务及其 repositories 限定在单个 context manager 内。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """保存 Session factory，但不提前创建连接或事务。

        Args:
            session_factory: 由 persistence composition root 创建的 factory。
        """
        self._session_factory = session_factory
        self._session: Session | None = None
        self._used = False

    def __enter__(self) -> UnitOfWork:
        """创建唯一 Session/事务并绑定全部 owner repositories。

        Returns:
            已进入短事务的当前 UnitOfWork。

        Raises:
            RuntimeError: 同一个 UnitOfWork 实例被嵌套或重复进入。
        """
        if self._used:
            raise RuntimeError("UnitOfWork 实例只能使用一次")
        self._used = True
        session = self._session_factory()
        try:
            session.begin()
            self._session = session
            self.china_announcements = ChinaAnnouncementRepository(session)
            self.cninfo_announcements = CninfoAnnouncementRepository(session)
            self.sse_announcements = SseAnnouncementRepository(session)
            self.szse_announcements = SzseAnnouncementRepository(session)
            self.china_matches = ChinaMatchRepository(session)
            self.china_summaries = ChinaSummaryRepository(session)
            self.telegram = TelegramRepository(session)
        except BaseException:
            session.close()
            self._session = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """正常提交、异常回滚，并在提交失败时仍回滚和关闭。

        Args:
            exc_type: `with` 块异常类型；正常退出时为 `None`。
            exc_value: `with` 块异常实例；正常退出时为 `None`。
            traceback: `with` 块异常 traceback；正常退出时为 `None`。

        Returns:
            始终为 `False`，不吞掉业务异常或提交失败。
        """
        session = self._require_session()
        try:
            if exc_type is None:
                try:
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
            else:
                session.rollback()
        finally:
            session.close()
            self._session = None
        return False

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """建立只回滚局部写入的 PostgreSQL nested transaction。

        Yields:
            保存点内的执行权；异常会回滚到保存点并继续由调用方决定是否捕获。

        Raises:
            RuntimeError: UnitOfWork 尚未进入。
        """
        session = self._require_session()
        with session.begin_nested():
            yield

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork 尚未进入")
        return self._session


def create_uow_factory(
    session_factory: sessionmaker[Session],
) -> Callable[[], UnitOfWork]:
    """创建每次调用都返回全新 UnitOfWork 的窄 factory。

    Args:
        session_factory: v2 persistence Session factory。

    Returns:
        可由 Stage 反复调用的无参数 UnitOfWork factory。
    """

    def factory() -> UnitOfWork:
        return UnitOfWork(session_factory)

    return factory
