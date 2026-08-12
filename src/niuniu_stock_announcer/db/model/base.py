"""SQLAlchemy Declarative 基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """汇总 v2 ORM metadata；DDL 仍只由 Alembic 创建。"""
