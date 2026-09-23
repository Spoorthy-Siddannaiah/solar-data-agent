"""Deterministic golden evals for the scoped agent data path.

These tests do not call an LLM. They verify that representative user intents
map to safe, scoped tools and that those tools enforce the expected security
and data-access outcomes. Live Deep Agents behavior should be evaluated
separately because wording and tool-choice timing are model-dependent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.agent.tools import build_scoped_tools, model_visible_tools
from backend.auth import load_user_context
from backend.database import create_session_factory, create_sqlite_engine
from backend.scripts.ingest_data import run_ingestion
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError


REPO_ROOT = Path(__file__).resolve().parents[1]
MARCH_START = "2026-03-01T00:00:00Z"
APRIL_START = "2026-04-01T00:00:00Z"


class DeterministicAgentGoldenEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.database = root / "golden-eval.db"
        cls.storage = root / "documents"
        run_ingestion(REPO_ROOT / "data", cls.database)
        cls.engine = create_sqlite_engine(cls.database)
        cls.session_factory = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def test_golden_matrix_scoped_tool_outcomes(self) -> None:
        cases = (
            {
                "name": "company_1_operator_lists_only_company_1_plants",
                "user": "company_1_operator",
                "query": "List my plants",
                "expected_tool": "list_plants",
                "arguments": {},
                "assertion": self._assert_company_1_plants,
            },
            {
                "name": "company_2_operator_lists_only_company_2_plants",
                "user": "company_2_operator",
                "query": "List my plants",
                "expected_tool": "list_plants",
                "arguments": {},
                "assertion": self._assert_company_2_plants,
            },
            {
                "name": "company_1_operator_gets_march_energy_for_plant_1001",
                "user": "company_1_operator",
                "query": "How much energy did plant 1001 produce in March?",
                "expected_tool": "get_energy_summary",
                "arguments": {
                    "start": MARCH_START,
                    "end": APRIL_START,
                    "plant_id": 1001,
                },
                "assertion": self._assert_plant_1001_march_energy,
            },
            {
                "name": "company_1_admin_compares_march_plants",
                "user": "company_1_admin",
                "query": "Compare my plants for March",
                "expected_tool": "compare_plants",
                "arguments": {
                    "start": MARCH_START,
                    "end": APRIL_START,
                    "plant_ids": None,
                },
                "assertion": self._assert_company_1_march_comparison,
            },
            {
                "name": "company_1_admin_gets_march_monthly_costs",
                "user": "company_1_admin",
                "query": "Show monthly costs for March",
                "expected_tool": "get_monthly_cost_summary",
                "arguments": {"month": 3},
                "assertion": self._assert_financial_summary_without_scope_leaks,
            },
            {
                "name": "company_1_operator_creates_energy_report",
                "user": "company_1_operator",
                "query": "Generate a PDF energy report",
                "expected_tool": "create_energy_report",
                "arguments": {
                    "format": "pdf",
                    "start": MARCH_START,
                    "end": APRIL_START,
                },
                "assertion": self._assert_safe_energy_report_metadata,
            },
            {
                "name": "company_1_admin_creates_financial_report",
                "user": "company_1_admin",
                "query": "Generate a PDF financial report for March",
                "expected_tool": "create_financial_report",
                "arguments": {"format": "pdf", "month": 3},
                "assertion": self._assert_safe_financial_report_metadata,
            },
        )

        for case in cases:
            with self.subTest(case=case["name"], query=case["query"]):
                result = self._invoke_visible_tool(
                    case["user"], case["expected_tool"], case["arguments"]
                )
                case["assertion"](result)

    def test_golden_matrix_financial_denials_are_enforced_without_llm(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            visible_names = {
                tool.name
                for tool in model_visible_tools(
                    build_scoped_tools(session, ctx, self.storage), ctx
                )
            }
            all_tools = {
                tool.name: tool
                for tool in build_scoped_tools(session, ctx, self.storage)
            }

            self.assertNotIn("get_market_price_summary", visible_names)
            self.assertNotIn("get_monthly_cost_summary", visible_names)
            self.assertNotIn("create_financial_report", visible_names)
            with self.assertRaises(PermissionDeniedError):
                all_tools["get_market_price_summary"].invoke(
                    {"start": MARCH_START, "end": APRIL_START}
                )
            with self.assertRaises(PermissionDeniedError):
                all_tools["get_monthly_cost_summary"].invoke({"month": 3})
            with self.assertRaises(PermissionDeniedError):
                all_tools["create_financial_report"].invoke(
                    {"format": "xlsx", "month": 3}
                )

    def test_golden_matrix_cross_tenant_plant_is_not_accessible(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            tools = {
                tool.name: tool
                for tool in model_visible_tools(
                    build_scoped_tools(session, ctx, self.storage), ctx
                )
            }

            with self.assertRaises(ResourceNotFoundError):
                tools["get_energy_summary"].invoke(
                    {
                        "start": MARCH_START,
                        "end": APRIL_START,
                        "plant_id": 2001,
                    }
                )

    def test_golden_matrix_prompt_injection_has_no_dangerous_tool_surface(
        self,
    ) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            tools = model_visible_tools(build_scoped_tools(session, ctx), ctx)

        tool_names = {tool.name.lower() for tool in tools}
        serialized_schemas = json.dumps(
            [tool.openai_schema() for tool in tools], sort_keys=True
        ).lower()
        self.assertFalse(
            any(
                forbidden in tool_name
                for tool_name in tool_names
                for forbidden in ("sql", "database", "filesystem", "shell")
            )
        )
        for forbidden in (
            "company_id",
            "user_id",
            "database_path",
            "filesystem_path",
            "file_path",
            "storage_key",
            "sql",
        ):
            self.assertNotIn(forbidden, serialized_schemas)

    def _invoke_visible_tool(
        self, user_id: str, expected_tool: str, arguments: dict
    ) -> dict:
        with self.session_factory.begin() as session:
            ctx = load_user_context(session, user_id=user_id)
            tools = {
                tool.name: tool
                for tool in model_visible_tools(
                    build_scoped_tools(session, ctx, self.storage), ctx
                )
            }
            self.assertIn(expected_tool, tools)
            return json.loads(tools[expected_tool].invoke(arguments))

    def _assert_company_1_plants(self, result: dict) -> None:
        self.assertEqual(
            [plant["plant_id"] for plant in result["plants"]], [1001, 1002]
        )
        self._assert_no_scope_leaks(result)
        self.assertNotIn("Plant C2-", json.dumps(result))

    def _assert_company_2_plants(self, result: dict) -> None:
        self.assertEqual(
            [plant["plant_id"] for plant in result["plants"]], [2001, 2002]
        )
        self._assert_no_scope_leaks(result)
        self.assertNotIn("Plant C1-", json.dumps(result))

    def _assert_plant_1001_march_energy(self, result: dict) -> None:
        summaries = result["plant_summaries"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["plant_id"], 1001)
        total_meter = next(
            metric
            for metric in summaries[0]["metrics"]
            if metric["datasource_id"] == 100101
        )
        self.assertEqual(total_meter["name"], "Total meter energy")
        self.assertEqual(total_meter["unit"], "kWh")
        self.assertEqual(total_meter["aggregation"], "sum")
        self.assertEqual(total_meter["sample_count"], 744)
        self.assertEqual(total_meter["value"], "185158.85800000")
        self._assert_no_scope_leaks(result)

    def _assert_company_1_march_comparison(self, result: dict) -> None:
        comparison = result["plant_comparison"]
        self.assertEqual([item["plant_id"] for item in comparison], [1001, 1002])
        self.assertEqual(comparison[0]["total_energy_kwh"], "185158.85800000")
        self.assertEqual(comparison[0]["sample_count"], 744)
        self.assertTrue(comparison[1]["total_energy_kwh"])
        self._assert_no_scope_leaks(result)

    def _assert_financial_summary_without_scope_leaks(self, result: dict) -> None:
        self.assertTrue(result["monthly_cost_summaries"])
        self._assert_no_scope_leaks(result)

    def _assert_safe_energy_report_metadata(self, result: dict) -> None:
        document = result["document"]
        self.assertEqual(document["classification"], "energy")
        self.assertTrue(document["download_url"].startswith("/documents/"))
        self._assert_no_scope_leaks(document)
        self.assertNotIn(str(self.storage), json.dumps(document))

    def _assert_safe_financial_report_metadata(self, result: dict) -> None:
        document = result["document"]
        self.assertEqual(document["classification"], "financial")
        self.assertTrue(document["download_url"].startswith("/documents/"))
        self._assert_no_scope_leaks(document)
        self.assertNotIn(str(self.storage), json.dumps(document))

    def _assert_no_scope_leaks(self, value: object) -> None:
        serialized = json.dumps(value)
        for forbidden in (
            "company_id",
            "user_id",
            "storage_key",
            "database_path",
            "filesystem_path",
            "file_path",
            str(self.database),
            str(self.storage),
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
