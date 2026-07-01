#!/usr/bin/env python3
"""Validate and ingest the local multi-tenant data package into SQLite."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

# Support both `python -m backend.scripts.ingest_data` and direct script usage.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.database import create_session_factory, create_sqlite_engine, reset_schema
from backend.models import (
    Company,
    DataSource,
    Element,
    MarketPrice,
    MonthlyCost,
    Plant,
    Reading,
    User,
)


ALLOWED_ACCESS_SCOPES = {"energy", "energy+financial"}
READING_FILENAME = re.compile(
    r"^GET_api_DataList_v2__ds_(?P<datasource_id>\d+)"
    r"__(?P<aggregation>sum|average)\.json$"
)

COUNT_MODELS = (
    ("companies", Company),
    ("users", User),
    ("plants", Plant),
    ("elements", Element),
    ("datasources", DataSource),
    ("readings", Reading),
    ("market_prices", MarketPrice),
    ("monthly_costs", MonthlyCost),
)


class IngestionError(ValueError):
    """Raised when source data violates the ingestion contract."""


def fail(path: Path, message: str) -> IngestionError:
    return IngestionError(f"{path}: {message}")


def require_fields(
    record: Mapping[str, Any], fields: Iterable[str], path: Path, location: str
) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise fail(path, f"{location} missing fields: {missing}")


def load_json(path: Path, expected_type: type) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise fail(path, f"cannot read JSON: {error}") from error
    if not isinstance(value, expected_type):
        raise fail(path, f"expected {expected_type.__name__}, got {type(value).__name__}")
    return value


def read_csv(path: Path, required_columns: Sequence[str]) -> List[Dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            missing = [column for column in required_columns if column not in columns]
            if missing:
                raise fail(path, f"missing CSV columns: {missing}")
            return [dict(row) for row in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise fail(path, f"cannot read CSV: {error}") from error


def nonempty(value: Any, path: Path, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise fail(path, f"{location} must be a non-empty string")
    return value.strip()


def integer(value: Any, path: Path, location: str) -> int:
    if isinstance(value, bool):
        raise fail(path, f"{location} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise fail(path, f"{location} must be an integer: {value!r}") from error
    if isinstance(value, float) and not value.is_integer():
        raise fail(path, f"{location} must be an integer: {value!r}")
    return result


def decimal_value(value: Any, path: Path, location: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise fail(path, f"{location} must be decimal: {value!r}") from error
    if not result.is_finite():
        raise fail(path, f"{location} must be finite: {value!r}")
    return result


def scaled_integer(
    value: Any, scale: int, path: Path, location: str
) -> int:
    decimal = decimal_value(value, path, location)
    scaled = decimal * scale
    if scaled != scaled.to_integral_value():
        raise fail(
            path,
            f"{location} has more precision than scale 1/{scale}: {value!r}",
        )
    return int(scaled)


def utc_timestamp(value: Any, path: Path, location: str) -> datetime:
    text = nonempty(value, path, location)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise fail(path, f"{location} is not an ISO 8601 timestamp: {text!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise fail(path, f"{location} must include a timezone: {text!r}")
    return parsed.astimezone(timezone.utc)


def iso_date(value: str, path: Path, location: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise fail(path, f"{location} is not an ISO date: {value!r}") from error


def parameter_map(record: Mapping[str, Any], path: Path, location: str) -> Dict[str, str]:
    raw_parameters = record.get("Parameters", [])
    if not isinstance(raw_parameters, list):
        raise fail(path, f"{location}.Parameters must be a list")
    parameters: Dict[str, str] = {}
    for index, item in enumerate(raw_parameters):
        if not isinstance(item, dict):
            raise fail(path, f"{location}.Parameters[{index}] must be an object")
        require_fields(item, ("Key", "Value"), path, f"{location}.Parameters[{index}]")
        key = nonempty(item["Key"], path, f"{location}.Parameters[{index}].Key")
        if key in parameters:
            raise fail(path, f"{location} has duplicate parameter {key!r}")
        parameters[key] = str(item["Value"])
    return parameters


def validate_trusted_company(company_dir: Path) -> Tuple[str, str]:
    path = company_dir / "company.json"
    record = load_json(path, dict)
    require_fields(record, ("company_id", "display_name"), path, "company")
    company_id = nonempty(record["company_id"], path, "company_id")
    if company_id != company_dir.name:
        raise fail(
            path,
            f"trusted company_id {company_id!r} does not match directory "
            f"{company_dir.name!r}",
        )
    return company_id, nonempty(record["display_name"], path, "display_name")


def ingest_users(session: Session, company_dir: Path, company_id: str) -> int:
    path = company_dir / "users.csv"
    rows = read_csv(path, ("user_id", "email", "role", "access_scope"))
    seen: Set[str] = set()
    for line, row in enumerate(rows, start=2):
        location = f"row {line}"
        user_id = nonempty(row["user_id"], path, f"{location}.user_id")
        if user_id in seen:
            raise fail(path, f"{location} duplicates user_id {user_id!r}")
        seen.add(user_id)
        access_scope = nonempty(
            row["access_scope"], path, f"{location}.access_scope"
        )
        if access_scope not in ALLOWED_ACCESS_SCOPES:
            raise fail(
                path,
                f"{location} has unknown access_scope {access_scope!r}; "
                f"allowed: {sorted(ALLOWED_ACCESS_SCOPES)}",
            )
        session.add(
            User(
                company_id=company_id,
                user_id=user_id,
                email=nonempty(row["email"], path, f"{location}.email"),
                role=nonempty(row["role"], path, f"{location}.role"),
                access_scope=access_scope,
                active=True,
            )
        )
    return len(rows)


def ingest_plants(
    session: Session, company_dir: Path, company_id: str
) -> Dict[int, Plant]:
    path = company_dir / "api" / "GET_api_Plant.json"
    records = load_json(path, list)
    plants: Dict[int, Plant] = {}
    for index, record in enumerate(records):
        location = f"record {index}"
        if not isinstance(record, dict):
            raise fail(path, f"{location} must be an object")
        require_fields(record, ("Id", "Name", "UniqueID"), path, location)
        plant_id = integer(record["Id"], path, f"{location}.Id")
        if plant_id in plants:
            raise fail(path, f"{location} duplicates plant ID {plant_id}")
        parameters = parameter_map(record, path, location)
        nominal_power = parameters.get("Nominal Power")
        commissioning = parameters.get("Commissioning Date")
        plant = Plant(
            company_id=company_id,
            plant_id=plant_id,
            name=nonempty(record["Name"], path, f"{location}.Name"),
            unique_id=nonempty(record["UniqueID"], path, f"{location}.UniqueID"),
            nominal_power_kw=(
                decimal_value(nominal_power, path, f"{location}.Nominal Power")
                if nominal_power is not None
                else None
            ),
            region=parameters.get("Region"),
            commissioning_date=(
                iso_date(commissioning, path, f"{location}.Commissioning Date")
                if commissioning is not None
                else None
            ),
        )
        plants[plant_id] = plant
        session.add(plant)
    return plants


def plant_directories(company_dir: Path, known_plants: Set[int]) -> Dict[int, Path]:
    api_dir = company_dir / "api"
    directories: Dict[int, Path] = {}
    for path in sorted(api_dir.glob("plant_*")):
        if not path.is_dir():
            continue
        try:
            plant_id = int(path.name.removeprefix("plant_"))
        except ValueError as error:
            raise fail(path, "plant directory suffix must be an integer") from error
        if plant_id not in known_plants:
            raise fail(path, f"directory references unknown plant {plant_id}")
        directories[plant_id] = path
    missing = known_plants - directories.keys()
    if missing:
        raise fail(api_dir, f"missing directories for plants: {sorted(missing)}")
    return directories


def ingest_elements(
    session: Session,
    company_id: str,
    directories: Mapping[int, Path],
) -> Dict[int, int]:
    elements: Dict[int, int] = {}
    for plant_id, directory in directories.items():
        path = directory / "GET_api_Plant_{plantId}_Element.json"
        records = load_json(path, list)
        for index, record in enumerate(records):
            location = f"record {index}"
            if not isinstance(record, dict):
                raise fail(path, f"{location} must be an object")
            require_fields(
                record,
                ("Identifier", "Name", "ParentId", "Type", "TypeString", "UniqueID"),
                path,
                location,
            )
            element_id = integer(
                record["Identifier"], path, f"{location}.Identifier"
            )
            parent_id = integer(record["ParentId"], path, f"{location}.ParentId")
            if parent_id != plant_id:
                raise fail(
                    path,
                    f"{location} references plant {parent_id}, expected {plant_id}",
                )
            if element_id in elements:
                raise fail(path, f"{location} duplicates element ID {element_id}")
            elements[element_id] = plant_id
            session.add(
                Element(
                    company_id=company_id,
                    element_id=element_id,
                    plant_id=plant_id,
                    name=nonempty(record["Name"], path, f"{location}.Name"),
                    type_code=integer(record["Type"], path, f"{location}.Type"),
                    type_name=nonempty(
                        record["TypeString"], path, f"{location}.TypeString"
                    ),
                    unique_id=nonempty(
                        record["UniqueID"], path, f"{location}.UniqueID"
                    ),
                )
            )
    return elements


def ingest_datasources(
    session: Session,
    company_id: str,
    directories: Mapping[int, Path],
    elements: Mapping[int, int],
) -> Dict[int, DataSource]:
    datasources: Dict[int, DataSource] = {}
    for plant_id, directory in directories.items():
        path = directory / "GET_api_Plant_{plantId}_Datasource.json"
        records = load_json(path, list)
        for index, record in enumerate(records):
            location = f"record {index}"
            if not isinstance(record, dict):
                raise fail(path, f"{location} must be an object")
            require_fields(
                record,
                ("DataSourceId", "DataSourceName", "ElementId", "Units"),
                path,
                location,
            )
            datasource_id = integer(
                record["DataSourceId"], path, f"{location}.DataSourceId"
            )
            element_id = integer(record["ElementId"], path, f"{location}.ElementId")
            if element_id not in elements:
                raise fail(path, f"{location} references unknown element {element_id}")
            if elements[element_id] != plant_id:
                raise fail(
                    path,
                    f"{location} references element {element_id} from another plant",
                )
            if datasource_id in datasources:
                raise fail(
                    path, f"{location} duplicates datasource ID {datasource_id}"
                )
            datasource = DataSource(
                company_id=company_id,
                datasource_id=datasource_id,
                element_id=element_id,
                name=nonempty(
                    record["DataSourceName"], path, f"{location}.DataSourceName"
                ),
                unit=nonempty(record["Units"], path, f"{location}.Units"),
                aggregation=None,
            )
            datasources[datasource_id] = datasource
            session.add(datasource)
    return datasources


def ingest_readings(
    session: Session,
    company_id: str,
    directories: Mapping[int, Path],
    elements: Mapping[int, int],
    datasources: Mapping[int, DataSource],
) -> int:
    reading_rows: List[Dict[str, Any]] = []
    seen_files: Set[int] = set()
    for plant_id, directory in directories.items():
        for path in sorted(directory.glob("GET_api_DataList_v2__ds_*__*.json")):
            match = READING_FILENAME.fullmatch(path.name)
            if match is None:
                raise fail(path, "reading filename does not match expected pattern")
            datasource_id = int(match.group("datasource_id"))
            aggregation = match.group("aggregation")
            datasource = datasources.get(datasource_id)
            if datasource is None:
                raise fail(path, f"filename references unknown datasource {datasource_id}")
            if elements[datasource.element_id] != plant_id:
                raise fail(
                    path,
                    f"datasource {datasource_id} belongs to another plant",
                )
            if datasource_id in seen_files:
                raise fail(path, f"multiple reading files for datasource {datasource_id}")
            seen_files.add(datasource_id)
            datasource.aggregation = aggregation

            records = load_json(path, list)
            seen_timestamps: Set[datetime] = set()
            for index, record in enumerate(records):
                location = f"record {index}"
                if not isinstance(record, dict):
                    raise fail(path, f"{location} must be an object")
                require_fields(
                    record, ("DataSourceId", "Date", "Value"), path, location
                )
                record_datasource_id = integer(
                    record["DataSourceId"], path, f"{location}.DataSourceId"
                )
                if record_datasource_id != datasource_id:
                    raise fail(
                        path,
                        f"{location} references datasource {record_datasource_id}, "
                        f"expected {datasource_id}",
                    )
                observed_at = utc_timestamp(
                    record["Date"], path, f"{location}.Date"
                )
                if observed_at in seen_timestamps:
                    raise fail(
                        path, f"{location} duplicates timestamp {observed_at.isoformat()}"
                    )
                seen_timestamps.add(observed_at)
                reading_rows.append(
                    {
                        "company_id": company_id,
                        "datasource_id": datasource_id,
                        "observed_at": observed_at,
                        "aggregation": aggregation,
                        "value": decimal_value(
                            record["Value"], path, f"{location}.Value"
                        ),
                    }
                )
    missing = set(datasources) - seen_files
    if missing:
        raise fail(
            next(iter(directories.values())).parent,
            f"missing reading files for datasources: {sorted(missing)}",
        )
    session.bulk_insert_mappings(Reading, reading_rows)
    return len(reading_rows)


def validate_row_company(
    row: Mapping[str, str],
    trusted_company_id: str,
    path: Path,
    location: str,
) -> None:
    source_company_id = nonempty(
        row.get("company_id"), path, f"{location}.company_id"
    )
    if source_company_id != trusted_company_id:
        raise fail(
            path,
            f"{location} company_id {source_company_id!r} does not match trusted "
            f"company_id {trusted_company_id!r}",
        )


def ingest_market_prices(
    session: Session, company_dir: Path, company_id: str
) -> int:
    path = company_dir / "financial" / "hourly_market_prices.csv"
    rows = read_csv(path, ("company_id", "zone", "timestamp", "eur_per_mwh"))
    mappings: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, datetime]] = set()
    for line, row in enumerate(rows, start=2):
        location = f"row {line}"
        validate_row_company(row, company_id, path, location)
        zone = nonempty(row["zone"], path, f"{location}.zone")
        observed_at = utc_timestamp(
            row["timestamp"], path, f"{location}.timestamp"
        )
        key = (zone, observed_at)
        if key in seen:
            raise fail(path, f"{location} duplicates zone/timestamp {key!r}")
        seen.add(key)
        mappings.append(
            {
                "company_id": company_id,
                "zone": zone,
                "observed_at": observed_at,
                "eur_per_mwh_micros": scaled_integer(
                    row["eur_per_mwh"],
                    1_000_000,
                    path,
                    f"{location}.eur_per_mwh",
                ),
            }
        )
    session.bulk_insert_mappings(MarketPrice, mappings)
    return len(mappings)


def ingest_monthly_costs(
    session: Session,
    company_dir: Path,
    company_id: str,
    known_plants: Set[int],
) -> int:
    path = company_dir / "financial" / "monthly_costs.csv"
    rows = read_csv(
        path,
        (
            "company_id",
            "plant_id",
            "year",
            "month",
            "category",
            "amount_eur",
            "notes",
        ),
    )
    mappings: List[Dict[str, Any]] = []
    for line, row in enumerate(rows, start=2):
        location = f"row {line}"
        validate_row_company(row, company_id, path, location)
        plant_id = integer(row["plant_id"], path, f"{location}.plant_id")
        if plant_id not in known_plants:
            raise fail(path, f"{location} references unknown plant {plant_id}")
        month = integer(row["month"], path, f"{location}.month")
        if not 1 <= month <= 12:
            raise fail(path, f"{location}.month must be between 1 and 12")
        notes = row["notes"].strip() or None
        mappings.append(
            {
                "company_id": company_id,
                "plant_id": plant_id,
                "year": integer(row["year"], path, f"{location}.year"),
                "month": month,
                "category": nonempty(
                    row["category"], path, f"{location}.category"
                ),
                "amount_eur_minor": scaled_integer(
                    row["amount_eur"],
                    100,
                    path,
                    f"{location}.amount_eur",
                ),
                "notes": notes,
            }
        )
    session.bulk_insert_mappings(MonthlyCost, mappings)
    return len(mappings)


def ingest_company(
    session: Session, company_dir: Path
) -> Tuple[str, Counter]:
    company_id, display_name = validate_trusted_company(company_dir)
    session.add(Company(company_id=company_id, display_name=display_name))
    session.flush()
    counts: Counter = Counter(companies=1)

    counts["users"] = ingest_users(session, company_dir, company_id)
    plants = ingest_plants(session, company_dir, company_id)
    counts["plants"] = len(plants)
    session.flush()

    directories = plant_directories(company_dir, set(plants))
    elements = ingest_elements(session, company_id, directories)
    counts["elements"] = len(elements)
    session.flush()

    datasources = ingest_datasources(
        session, company_id, directories, elements
    )
    counts["datasources"] = len(datasources)
    session.flush()

    counts["readings"] = ingest_readings(
        session, company_id, directories, elements, datasources
    )
    counts["market_prices"] = ingest_market_prices(
        session, company_dir, company_id
    )
    counts["monthly_costs"] = ingest_monthly_costs(
        session, company_dir, company_id, set(plants)
    )
    session.flush()
    return company_id, counts


def company_directories(data_dir: Path) -> List[Path]:
    directories = sorted(
        path
        for path in data_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    if not directories:
        raise IngestionError(f"{data_dir}: no company directories found")
    return directories


def verify_database_counts(
    session: Session, expected: Mapping[str, Counter]
) -> None:
    for company_id, company_counts in expected.items():
        for label, model in COUNT_MODELS:
            statement = select(func.count()).select_from(model)
            if label == "companies":
                statement = statement.where(Company.company_id == company_id)
            else:
                statement = statement.where(model.company_id == company_id)
            actual = session.scalar(statement)
            expected_count = company_counts[label]
            if actual != expected_count:
                raise IngestionError(
                    f"database count mismatch for {company_id}.{label}: "
                    f"expected {expected_count}, got {actual}"
                )


def print_counts(counts: Mapping[str, Counter], database_path: Path) -> None:
    print(f"Ingestion complete: {database_path}")
    for company_id, company_counts in counts.items():
        print(f"\n{company_id}:")
        for label, _ in COUNT_MODELS:
            print(f"  {label}: {company_counts[label]}")


def run_ingestion(data_dir: Path, database_path: Path) -> Dict[str, Counter]:
    data_dir = data_dir.expanduser().resolve()
    database_path = database_path.expanduser().resolve()
    if not data_dir.is_dir():
        raise IngestionError(f"Data directory does not exist: {data_dir}")
    if database_path == data_dir or data_dir in database_path.parents:
        raise IngestionError("Database path must not be inside the source data directory")

    engine: Optional[Engine] = None
    try:
        # Importing models above registers them with Base.metadata.
        engine = create_sqlite_engine(database_path)
        reset_schema(engine)
        session_factory = create_session_factory(engine)
        counts: Dict[str, Counter] = {}
        with session_factory.begin() as session:
            for directory in company_directories(data_dir):
                company_id, company_counts = ingest_company(session, directory)
                if company_id in counts:
                    raise IngestionError(f"duplicate company_id {company_id!r}")
                counts[company_id] = company_counts
            verify_database_counts(session, counts)
        return counts
    finally:
        if engine is not None:
            engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset and ingest the local data package into SQLite."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="source data directory (default: data)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("backend/solar_data.db"),
        help="SQLite database path (default: backend/solar_data.db)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        counts = run_ingestion(args.data_dir, args.database)
    except (IngestionError, OSError) as error:
        print(f"Ingestion failed: {error}", file=sys.stderr)
        return 1
    print_counts(counts, args.database.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
