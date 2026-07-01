"""Tenant-scoped financial queries with capability enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import UserContext
from backend.models import MarketPrice, MonthlyCost, Plant
from backend.services.errors import PermissionDeniedError


@dataclass(frozen=True)
class MarketPriceSummary:
    zone: str
    sample_count: int
    minimum_eur_per_mwh_micros: int
    maximum_eur_per_mwh_micros: int
    average_eur_per_mwh_micros: int


@dataclass(frozen=True)
class MonthlyCostSummary:
    year: int
    month: int
    category: str
    amount_eur_minor: int


class FinanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_market_price_summary(
        self,
        ctx: UserContext,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Tuple[MarketPriceSummary, ...]:
        self._require_financials(ctx)
        start, end = self._validate_range(start, end)
        statement = (
            select(
                MarketPrice.zone,
                func.count(MarketPrice.eur_per_mwh_micros),
                func.min(MarketPrice.eur_per_mwh_micros),
                func.max(MarketPrice.eur_per_mwh_micros),
                func.avg(MarketPrice.eur_per_mwh_micros),
            )
            .where(MarketPrice.company_id == ctx.company_id)
            .group_by(MarketPrice.zone)
            .order_by(MarketPrice.zone)
        )
        if start is not None:
            statement = statement.where(MarketPrice.observed_at >= start)
        if end is not None:
            statement = statement.where(MarketPrice.observed_at < end)

        return tuple(
            MarketPriceSummary(
                zone=row[0],
                sample_count=row[1],
                minimum_eur_per_mwh_micros=row[2],
                maximum_eur_per_mwh_micros=row[3],
                average_eur_per_mwh_micros=round(row[4]),
            )
            for row in self._session.execute(statement)
        )

    def get_monthly_cost_summary(
        self,
        ctx: UserContext,
        month: Optional[int] = None,
    ) -> Tuple[MonthlyCostSummary, ...]:
        self._require_financials(ctx)
        if month is not None and not 1 <= month <= 12:
            raise ValueError("month must be between 1 and 12")

        statement = (
            select(
                MonthlyCost.year,
                MonthlyCost.month,
                MonthlyCost.category,
                func.sum(MonthlyCost.amount_eur_minor),
            )
            .join(
                Plant,
                (Plant.company_id == MonthlyCost.company_id)
                & (Plant.plant_id == MonthlyCost.plant_id),
            )
            .where(
                MonthlyCost.company_id == ctx.company_id,
                Plant.company_id == ctx.company_id,
            )
            .group_by(
                MonthlyCost.year,
                MonthlyCost.month,
                MonthlyCost.category,
            )
            .order_by(
                MonthlyCost.year,
                MonthlyCost.month,
                MonthlyCost.category,
            )
        )
        if month is not None:
            statement = statement.where(MonthlyCost.month == month)

        return tuple(
            MonthlyCostSummary(
                year=row[0],
                month=row[1],
                category=row[2],
                amount_eur_minor=row[3],
            )
            for row in self._session.execute(statement)
        )

    @staticmethod
    def _require_financials(ctx: UserContext) -> None:
        if not ctx.can_view_financials:
            raise PermissionDeniedError("financial data access is not permitted")

    @staticmethod
    def _validate_range(
        start: Optional[datetime], end: Optional[datetime]
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        normalized = []
        for label, value in (("start", start), ("end", end)):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{label} must be timezone-aware")
            normalized.append(
                value.astimezone(timezone.utc) if value is not None else None
            )
        normalized_start, normalized_end = normalized
        if (
            normalized_start is not None
            and normalized_end is not None
            and normalized_start >= normalized_end
        ):
            raise ValueError("start must be before end")
        return normalized_start, normalized_end
