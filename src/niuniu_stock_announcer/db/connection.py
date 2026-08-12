"""SQLAlchemy Engine 与 Session factory。"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str) -> Engine:
    """创建同步 PostgreSQL Engine，不执行连接或 schema 修改。

    Args:
        database_url: 必须显式使用 psycopg 3 driver 的数据库 URL。

    Returns:
        延迟建立物理连接的 SQLAlchemy Engine。

    Raises:
        ValueError: URL 不是 `postgresql+psycopg`。
    """
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL 必须使用 postgresql+psycopg driver")
    return create_engine(url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建一次只服务一个短 UnitOfWork 的 Session factory。

    Args:
        engine: v2 PostgreSQL 同步 Engine。

    Returns:
        禁止提交后隐式刷新 ORM 状态的 Session factory。
    """
    return sessionmaker(bind=engine, expire_on_commit=False)
