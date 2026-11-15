from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 제약 이름을 고정해야 Alembic 리비전이 환경마다 같은 이름을 만든다.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_timestamp() -> DateTime:
    return DateTime(timezone=True)


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """DB에는 문자열(VARCHAR) + CHECK 제약으로 저장한다(네이티브 ENUM 타입은 변경이 번거롭다)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        utc_timestamp(), server_default=func.now(), onupdate=func.now()
    )
