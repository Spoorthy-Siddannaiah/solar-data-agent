"""Tests for trusted user context loading and tenant-scoped read services."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from backend.auth import load_user_context
from backend.database import create_session_factory, create_sqlite_engine
from backend.scripts.ingest_data import run_ingestion
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError
from backend.services.finance import FinanceService
from backend.services.plants import PlantDataService


REPO_ROOT = Path(__file__).resolve().parents[1]


class ScopedServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.database = Path(cls.temp_dir.name) / "services.db"
        run_ingestion(REPO_ROOT / "data", cls.database)
        cls.engine = create_sqlite_engine(cls.database)
        cls.session_factory = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def test_company_1_user_sees_only_company_1_plants(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            plants = PlantDataService(session).list_plants(ctx)

        self.assertEqual([plant.plant_id for plant in plants], [1001, 1002])
        self.assertTrue(all(plant.name.startswith("Plant C1-") for plant in plants))

    def test_company_2_user_sees_only_company_2_plants(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(
                session, email="operator.company_2@example.com"
            )
            plants = PlantDataService(session).list_plants(ctx)

        self.assertEqual([plant.plant_id for plant in plants], [2001, 2002])
        self.assertTrue(all(plant.name.startswith("Plant C2-") for plant in plants))

    def test_energy_only_user_cannot_access_market_prices(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            with self.assertRaisesRegex(
                PermissionDeniedError, "financial data access is not permitted"
            ):
                FinanceService(session).get_market_price_summary(ctx)

    def test_energy_only_user_cannot_access_monthly_costs(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            with self.assertRaisesRegex(
                PermissionDeniedError, "financial data access is not permitted"
            ):
                FinanceService(session).get_monthly_cost_summary(ctx)

    def test_financial_user_can_access_market_prices(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            summaries = FinanceService(session).get_market_price_summary(ctx)

        self.assertTrue(ctx.can_view_financials)
        self.assertEqual(sum(item.sample_count for item in summaries), 2_928)
        self.assertTrue(
            all(item.minimum_eur_per_mwh_micros > 0 for item in summaries)
        )

    def test_financial_user_can_access_monthly_costs(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_2_admin")
            summaries = FinanceService(session).get_monthly_cost_summary(ctx, month=3)

        self.assertTrue(ctx.can_view_financials)
        self.assertTrue(summaries)
        self.assertTrue(all(item.month == 3 for item in summaries))
        self.assertTrue(all(item.amount_eur_minor > 0 for item in summaries))

    def test_cross_company_plant_id_is_not_accessible(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            with self.assertRaisesRegex(
                ResourceNotFoundError, "not found or are not accessible"
            ):
                PlantDataService(session).get_energy_summary(
                    ctx, plant_id=2001
                )

    def test_energy_summary_and_comparison_are_tenant_scoped(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            service = PlantDataService(session)
            summaries = service.get_energy_summary(ctx)
            comparison = service.compare_plants(ctx)

        self.assertEqual([item.plant_id for item in summaries], [1001, 1002])
        self.assertTrue(all(len(item.metrics) == 5 for item in summaries))
        self.assertEqual([item.plant_id for item in comparison], [1001, 1002])
        self.assertTrue(all(item.sample_count == 1_464 for item in comparison))

    def test_public_service_methods_do_not_accept_company_id(self) -> None:
        methods = (
            PlantDataService.list_plants,
            PlantDataService.get_energy_summary,
            PlantDataService.compare_plants,
            FinanceService.get_market_price_summary,
            FinanceService.get_monthly_cost_summary,
        )
        for method in methods:
            with self.subTest(method=method.__qualname__):
                parameters = inspect.signature(method).parameters
                self.assertNotIn("company_id", parameters)

    def test_user_context_loader_does_not_accept_company_id(self) -> None:
        parameters = inspect.signature(load_user_context).parameters
        self.assertNotIn("company_id", parameters)


if __name__ == "__main__":
    unittest.main()
