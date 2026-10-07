"""SQLite defaultne, Postgres jen zmenou DATABASE_URL."""
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, LargeBinary, String, create_engine, inspect
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


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AresRaw(Base):
    """Surova odpoved ARES tak, jak prisla (nemeni se, jen se pridava/obnovuje)."""

    __tablename__ = "ares_raw"

    ico: Mapped[str] = mapped_column(String(8), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CompanyClean(Base):
    """Vycistena, typovana tabulka - nad ni stoji semanticka vrstva."""

    __tablename__ = "companies_clean"

    ico: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String)
    name_norm: Mapped[str] = mapped_column(String)
    legal_form_code: Mapped[str | None]
    legal_form: Mapped[str | None]
    street: Mapped[str | None]
    city: Mapped[str | None]
    zip: Mapped[str | None]
    founded: Mapped[date | None] = mapped_column(Date)
    dissolved: Mapped[date | None] = mapped_column(Date)
    founded_year: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String)
    age_years: Mapped[float | None] = mapped_column(Float)
    age_band: Mapped[str] = mapped_column(String)
    dic: Mapped[str | None]
    has_dic: Mapped[bool] = mapped_column(Boolean)
    nace_main: Mapped[str | None]
    nace_division: Mapped[str | None]
    nace_division_label: Mapped[str | None]
    nace_codes: Mapped[list] = mapped_column(JSON, default=list)
    quality_issues: Mapped[list] = mapped_column(JSON, default=list)
    source_hash: Mapped[str] = mapped_column(String(64))
    cleaned_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CompanyVector(Base):
    """Embedding firmy. Klic (ico, model): vektory ruznych modelu se nikdy nemichaji."""

    __tablename__ = "company_vectors"

    ico: Mapped[str] = mapped_column(String(8), primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    text: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


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
