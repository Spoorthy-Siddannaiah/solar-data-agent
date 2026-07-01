"""Integration tests for resettable demo-data ingestion."""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.database import create_session_factory, create_sqlite_engine, reset_schema
from backend.models import Company, DataSource, Element, MonthlyCost, Plant, Reading
from backend.scripts.ingest_data import IngestionError, run_ingestion


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATA = REPO_ROOT / "data"


class IngestionTests(unittest.TestCase):
    def test_ingests_expected_counts_and_storage_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "solar.db"
            counts = run_ingestion(SOURCE_DATA, database)

            self.assertEqual(counts["company_1"]["readings"], 14_640)
            self.assertEqual(counts["company_2"]["market_prices"], 2_928)
            self.assertEqual(counts["company_2"]["monthly_costs"], 28)

            connection = sqlite3.connect(database)
            try:
                timestamp, aggregation = connection.execute(
                    "SELECT observed_at, aggregation FROM readings LIMIT 1"
                ).fetchone()
                amount_minor = connection.execute(
                    "SELECT amount_eur_minor FROM monthly_costs LIMIT 1"
                ).fetchone()[0]
                price_micros = connection.execute(
                    "SELECT eur_per_mwh_micros FROM market_prices LIMIT 1"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertTrue(timestamp.endswith("Z"))
            self.assertIn(aggregation, {"sum", "average"})
            self.assertIsInstance(amount_minor, int)
            self.assertIsInstance(price_micros, int)

    def test_rejects_unknown_access_scope(self) -> None:
        def mutate(company_dir: Path) -> None:
            path = company_dir / "users.csv"
            rows = self._read_csv(path)
            rows[0]["access_scope"] = "unknown"
            self._write_csv(path, rows)

        self._assert_invalid(mutate, "unknown access_scope")

    def test_rejects_mismatched_financial_company(self) -> None:
        def mutate(company_dir: Path) -> None:
            path = company_dir / "financial" / "hourly_market_prices.csv"
            rows = self._read_csv(path)
            rows[0]["company_id"] = "company_2"
            self._write_csv(path, rows)

        self._assert_invalid(mutate, "does not match trusted company_id")

    def test_rejects_unknown_element_plant(self) -> None:
        def mutate(company_dir: Path) -> None:
            path = (
                company_dir
                / "api"
                / "plant_1001"
                / "GET_api_Plant_{plantId}_Element.json"
            )
            records = self._read_json(path)
            records[0]["ParentId"] = 9999
            path.write_text(json.dumps(records), encoding="utf-8")

        self._assert_invalid(mutate, "references plant 9999")

    def test_rejects_unknown_datasource_element(self) -> None:
        def mutate(company_dir: Path) -> None:
            path = (
                company_dir
                / "api"
                / "plant_1001"
                / "GET_api_Plant_{plantId}_Datasource.json"
            )
            records = self._read_json(path)
            records[0]["ElementId"] = 9999
            path.write_text(json.dumps(records), encoding="utf-8")

        self._assert_invalid(mutate, "references unknown element 9999")

    def test_rejects_unknown_reading_datasource(self) -> None:
        def mutate(company_dir: Path) -> None:
            path = (
                company_dir
                / "api"
                / "plant_1001"
                / "GET_api_DataList_v2__ds_100101__sum.json"
            )
            records = self._read_json(path)
            records[0]["DataSourceId"] = 9999
            path.write_text(json.dumps(records), encoding="utf-8")

        self._assert_invalid(mutate, "references datasource 9999")

    def test_rejects_unknown_monthly_cost_plant(self) -> None:
        def mutate(company_dir: Path) -> None:
            path = company_dir / "financial" / "monthly_costs.csv"
            rows = self._read_csv(path)
            rows[0]["plant_id"] = "9999"
            self._write_csv(path, rows)

        self._assert_invalid(mutate, "references unknown plant 9999")

    def _assert_invalid(
        self, mutate: Callable[[Path], None], message: str
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            company_dir = data_dir / "company_1"
            shutil.copytree(SOURCE_DATA / "company_1", company_dir)
            mutate(company_dir)
            with self.assertRaisesRegex(IngestionError, message):
                run_ingestion(data_dir, Path(temp_dir) / "solar.db")

    @staticmethod
    def _read_json(path: Path) -> list:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _read_csv(path: Path) -> list:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _write_csv(path: Path, rows: list) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


class CrossCompanyIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "integrity.db"
        self.engine = create_sqlite_engine(database)
        reset_schema(self.engine)
        self.session_factory = create_session_factory(self.engine)

        with self.engine.connect() as connection:
            foreign_keys = connection.scalar(text("PRAGMA foreign_keys"))
        self.assertEqual(foreign_keys, 1)

        with self.session_factory.begin() as session:
            session.add_all(
                [
                    Company(company_id="company_1", display_name="Company 1"),
                    Company(company_id="company_2", display_name="Company 2"),
                ]
            )
            session.flush()
            session.add(
                Plant(
                    company_id="company_2",
                    plant_id=2001,
                    name="Company 2 Plant",
                    unique_id="company-2-plant",
                    nominal_power_kw=None,
                    region=None,
                    commissioning_date=None,
                )
            )
            session.flush()
            session.add(
                Element(
                    company_id="company_2",
                    element_id=20001,
                    plant_id=2001,
                    name="Company 2 Element",
                    type_code=8,
                    type_name="TOTALIZERS",
                    unique_id="company-2-element",
                )
            )
            session.flush()
            session.add(
                DataSource(
                    company_id="company_2",
                    datasource_id=200001,
                    element_id=20001,
                    name="Company 2 Datasource",
                    unit="kWh",
                    aggregation="sum",
                )
            )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_company_1_element_cannot_reference_company_2_plant(self) -> None:
        invalid = Element(
            company_id="company_1",
            element_id=10001,
            plant_id=2001,
            name="Invalid Element",
            type_code=8,
            type_name="TOTALIZERS",
            unique_id="invalid-element",
        )
        self._assert_foreign_key_rejected(invalid)

    def test_company_1_datasource_cannot_reference_company_2_element(self) -> None:
        invalid = DataSource(
            company_id="company_1",
            datasource_id=100001,
            element_id=20001,
            name="Invalid Datasource",
            unit="kWh",
            aggregation="sum",
        )
        self._assert_foreign_key_rejected(invalid)

    def test_company_1_reading_cannot_reference_company_2_datasource(self) -> None:
        invalid = Reading(
            company_id="company_1",
            datasource_id=200001,
            observed_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            aggregation="sum",
            value=Decimal("1.0"),
        )
        self._assert_foreign_key_rejected(invalid)

    def test_company_1_monthly_cost_cannot_reference_company_2_plant(self) -> None:
        invalid = MonthlyCost(
            company_id="company_1",
            plant_id=2001,
            year=2026,
            month=3,
            category="maintenance",
            amount_eur_minor=100,
            notes=None,
        )
        self._assert_foreign_key_rejected(invalid)

    def _assert_foreign_key_rejected(self, invalid: object) -> None:
        with self.session_factory() as session:
            session.add(invalid)
            with self.assertRaises(IntegrityError) as raised:
                session.commit()
            self.assertIn("FOREIGN KEY constraint failed", str(raised.exception))
            session.rollback()


if __name__ == "__main__":
    unittest.main()
