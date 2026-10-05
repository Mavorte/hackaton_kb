"""SQLite defaultne, Postgres jen zmenou DATABASE_URL."""
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import JSON, DateTime, String, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from kb.clients.ares import Company
from kb.config import get_settings


class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    __tablename__ = "companies"

    ico: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String)
    legal_form: Mapped[str | None]
    address: Mapped[str | None]
    founded: Mapped[str | None]
    dissolved: Mapped[str | None]
    dic: Mapped[str | None]
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine


def upsert_company(c: Company, engine: Engine | None = None) -> None:
    with Session(engine or get_engine()) as s:
        s.merge(
            CompanyRow(
                ico=c.ico, name=c.name, legal_form=c.legal_form, address=c.address,
                founded=c.founded, dissolved=c.dissolved, dic=c.dic, raw=c.raw,
            )
        )
        s.commit()


def table_names(engine: Engine | None = None) -> list[str]:
    return inspect(engine or get_engine()).get_table_names()
