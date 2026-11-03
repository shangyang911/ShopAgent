"""[数据库模型基类] 商品等 SQLAlchemy 模型继承此 Base，以供启动时统一建表。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 SQLAlchemy ORM 模型共用的元数据基类。"""

    pass
