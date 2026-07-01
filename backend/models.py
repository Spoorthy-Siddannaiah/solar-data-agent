"""Tenant-qualified SQLAlchemy models for imported solar data."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import String as StringType
from sqlalchemy.types import TypeDecorator

from backend.database import Base


class UTCDateTime(TypeDecorator):
    """Persist aware datetimes as normalized UTC ISO 8601 text in SQLite."""

    impl = StringType(32)
    cache_ok = True

    def process_bind_param(
        self, value: Optional[datetime], dialect: object
    ) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def process_result_value(
        self, value: Optional[str], dialect: object
    ) -> Optional[datetime]:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "user_id"),
        UniqueConstraint("company_id", "email"),
        ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
    )

    company_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(50))
    access_scope: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Plant(Base):
    __tablename__ = "plants"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "plant_id"),
        UniqueConstraint("company_id", "unique_id"),
        ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        Index("ix_plants_company_name", "company_id", "name"),
    )

    company_id: Mapped[str] = mapped_column(String(100))
    plant_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    unique_id: Mapped[str] = mapped_column(String(100))
    nominal_power_kw: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    region: Mapped[Optional[str]] = mapped_column(String(100))
    commissioning_date: Mapped[Optional[date]] = mapped_column(Date)


class Element(Base):
    __tablename__ = "elements"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "element_id"),
        UniqueConstraint("company_id", "unique_id"),
        ForeignKeyConstraint(
            ["company_id", "plant_id"],
            ["plants.company_id", "plants.plant_id"],
        ),
        Index("ix_elements_company_plant", "company_id", "plant_id"),
    )

    company_id: Mapped[str] = mapped_column(String(100))
    element_id: Mapped[int] = mapped_column(Integer)
    plant_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    type_code: Mapped[int] = mapped_column(Integer)
    type_name: Mapped[str] = mapped_column(String(100))
    unique_id: Mapped[str] = mapped_column(String(100))


class DataSource(Base):
    __tablename__ = "datasources"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "datasource_id"),
        ForeignKeyConstraint(
            ["company_id", "element_id"],
            ["elements.company_id", "elements.element_id"],
        ),
        Index("ix_datasources_company_element", "company_id", "element_id"),
    )

    company_id: Mapped[str] = mapped_column(String(100))
    datasource_id: Mapped[int] = mapped_column(Integer)
    element_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(50))
    aggregation: Mapped[Optional[str]] = mapped_column(String(20))


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "company_id", "datasource_id", "observed_at", "aggregation"
        ),
        ForeignKeyConstraint(
            ["company_id", "datasource_id"],
            ["datasources.company_id", "datasources.datasource_id"],
        ),
        Index(
            "ix_readings_company_datasource_time",
            "company_id",
            "datasource_id",
            "observed_at",
        ),
    )

    company_id: Mapped[str] = mapped_column(String(100))
    datasource_id: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    aggregation: Mapped[str] = mapped_column(String(20))
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8))


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "zone", "observed_at"),
        ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        Index("ix_market_prices_company_time", "company_id", "observed_at"),
    )

    company_id: Mapped[str] = mapped_column(String(100))
    zone: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    eur_per_mwh_micros: Mapped[int] = mapped_column(Integer)


class MonthlyCost(Base):
    __tablename__ = "monthly_costs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "plant_id"],
            ["plants.company_id", "plants.plant_id"],
        ),
        Index(
            "ix_monthly_costs_company_plant_period",
            "company_id",
            "plant_id",
            "year",
            "month",
        ),
    )

    cost_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(String(100))
    plant_id: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(100))
    amount_eur_minor: Mapped[int] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", "run_id"),
        ForeignKeyConstraint(
            ["company_id", "user_id"],
            ["users.company_id", "users.user_id"],
        ),
        Index("ix_agent_runs_owner", "company_id", "user_id", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(String(100))
    run_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    result_text: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "user_id", "run_id"],
            ["agent_runs.company_id", "agent_runs.user_id", "agent_runs.run_id"],
        ),
        Index(
            "ix_generated_documents_owner",
            "company_id",
            "user_id",
            "run_id",
            "document_id",
        ),
    )

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(String(100))
    run_id: Mapped[str] = mapped_column(String(36))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    media_type: Mapped[str] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    classification: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
