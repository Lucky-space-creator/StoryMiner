"""
ORM 基类

整体思路：
    所有业务表共用一个 Declarative Base，便于元数据统一管理。

关键点：
    1. 仅导出 Base，供各模型继承。
    2. 时间字段统一由数据库触发器维护 updated_at（见 V1 SQL），模型不再重复。

实现逻辑：
    使用 sqlalchemy.orm DeclarativeBase。
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""
    pass
