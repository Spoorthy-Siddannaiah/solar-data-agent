"""Tenant-scoped operational plant queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.auth import UserContext
from backend.models import DataSource, Element, Plant, Reading
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError


@dataclass(frozen=True)
class PlantView:
    plant_id: int
    name: str
    nominal_power_kw: Optional[Decimal]
    region: Optional[str]
    commissioning_date: Optional[date]


@dataclass(frozen=True)
class EnergyMetricSummary:
    datasource_id: int
    name: str
    unit: str
    aggregation: str
    sample_count: int
    value: Decimal


@dataclass(frozen=True)
class PlantEnergySummary:
    plant_id: int
    plant_name: str
    metrics: Tuple[EnergyMetricSummary, ...]


@dataclass(frozen=True)
class PlantComparison:
    plant_id: int
    plant_name: str
    total_energy_kwh: Optional[Decimal]
    sample_count: int


class PlantDataService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_plants(self, ctx: UserContext) -> Tuple[PlantView, ...]:
        self._require_energy(ctx)
        statement = (
            select(Plant)
            .where(Plant.company_id == ctx.company_id)
            .order_by(Plant.plant_id)
        )
        return tuple(
            PlantView(
                plant_id=plant.plant_id,
                name=plant.name,
                nominal_power_kw=plant.nominal_power_kw,
                region=plant.region,
                commissioning_date=plant.commissioning_date,
            )
            for plant in self._session.scalars(statement)
        )

    def get_energy_summary(
        self,
        ctx: UserContext,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        plant_id: Optional[int] = None,
    ) -> Tuple[PlantEnergySummary, ...]:
        self._require_energy(ctx)
        start, end = self._validate_range(start, end)
        plant_ids = None if plant_id is None else (plant_id,)
        plants = self._scoped_plants(ctx, plant_ids)

        statement = (
            select(
                Plant.plant_id,
                DataSource.datasource_id,
                DataSource.name,
                DataSource.unit,
                Reading.aggregation,
                func.count(Reading.value),
                func.sum(Reading.value),
                func.avg(Reading.value),
            )
            .select_from(Reading)
            .join(
                DataSource,
                and_(
                    DataSource.company_id == Reading.company_id,
                    DataSource.datasource_id == Reading.datasource_id,
                ),
            )
            .join(
                Element,
                and_(
                    Element.company_id == DataSource.company_id,
                    Element.element_id == DataSource.element_id,
                ),
            )
            .join(
                Plant,
                and_(
                    Plant.company_id == Element.company_id,
                    Plant.plant_id == Element.plant_id,
                ),
            )
            .where(
                Reading.company_id == ctx.company_id,
                DataSource.company_id == ctx.company_id,
                Element.company_id == ctx.company_id,
                Plant.company_id == ctx.company_id,
                Plant.plant_id.in_(plants),
            )
            .group_by(
                Plant.plant_id,
                DataSource.datasource_id,
                DataSource.name,
                DataSource.unit,
                Reading.aggregation,
            )
            .order_by(Plant.plant_id, DataSource.datasource_id)
        )
        if start is not None:
            statement = statement.where(Reading.observed_at >= start)
        if end is not None:
            statement = statement.where(Reading.observed_at < end)

        metrics: Dict[int, List[EnergyMetricSummary]] = {
            scoped_plant_id: [] for scoped_plant_id in plants
        }
        for row in self._session.execute(statement):
            aggregate_value = row[6] if row[4] == "sum" else row[7]
            metrics[row[0]].append(
                EnergyMetricSummary(
                    datasource_id=row[1],
                    name=row[2],
                    unit=row[3],
                    aggregation=row[4],
                    sample_count=row[5],
                    value=Decimal(str(aggregate_value)),
                )
            )

        return tuple(
            PlantEnergySummary(
                plant_id=scoped_plant_id,
                plant_name=plants[scoped_plant_id],
                metrics=tuple(metrics[scoped_plant_id]),
            )
            for scoped_plant_id in sorted(plants)
        )

    def compare_plants(
        self,
        ctx: UserContext,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        plant_ids: Optional[Sequence[int]] = None,
    ) -> Tuple[PlantComparison, ...]:
        self._require_energy(ctx)
        start, end = self._validate_range(start, end)
        plants = self._scoped_plants(ctx, plant_ids)

        statement = (
            select(
                Plant.plant_id,
                func.sum(Reading.value),
                func.count(Reading.value),
            )
            .select_from(Reading)
            .join(
                DataSource,
                and_(
                    DataSource.company_id == Reading.company_id,
                    DataSource.datasource_id == Reading.datasource_id,
                ),
            )
            .join(
                Element,
                and_(
                    Element.company_id == DataSource.company_id,
                    Element.element_id == DataSource.element_id,
                ),
            )
            .join(
                Plant,
                and_(
                    Plant.company_id == Element.company_id,
                    Plant.plant_id == Element.plant_id,
                ),
            )
            .where(
                Reading.company_id == ctx.company_id,
                DataSource.company_id == ctx.company_id,
                Element.company_id == ctx.company_id,
                Plant.company_id == ctx.company_id,
                Plant.plant_id.in_(plants),
                DataSource.name == "Total meter energy",
                DataSource.unit == "kWh",
                Reading.aggregation == "sum",
            )
            .group_by(Plant.plant_id)
            .order_by(Plant.plant_id)
        )
        if start is not None:
            statement = statement.where(Reading.observed_at >= start)
        if end is not None:
            statement = statement.where(Reading.observed_at < end)

        values = {
            row[0]: (Decimal(str(row[1])), row[2])
            for row in self._session.execute(statement)
        }
        return tuple(
            PlantComparison(
                plant_id=scoped_plant_id,
                plant_name=plants[scoped_plant_id],
                total_energy_kwh=values.get(scoped_plant_id, (None, 0))[0],
                sample_count=values.get(scoped_plant_id, (None, 0))[1],
            )
            for scoped_plant_id in sorted(plants)
        )

    def _scoped_plants(
        self, ctx: UserContext, requested_ids: Optional[Iterable[int]]
    ) -> Dict[int, str]:
        statement = select(Plant.plant_id, Plant.name).where(
            Plant.company_id == ctx.company_id
        )
        expected = None
        if requested_ids is not None:
            expected = set(requested_ids)
            if not expected:
                return {}
            statement = statement.where(Plant.plant_id.in_(expected))
        plants = dict(self._session.execute(statement).all())
        if expected is not None and set(plants) != expected:
            raise ResourceNotFoundError(
                "one or more plants were not found or are not accessible"
            )
        return plants

    @staticmethod
    def _require_energy(ctx: UserContext) -> None:
        if not ctx.can_view_energy:
            raise PermissionDeniedError("energy data access is not permitted")

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
