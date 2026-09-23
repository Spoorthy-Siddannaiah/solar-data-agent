"""Mocked Deep Agents orchestration evals.

These tests do not call OpenAI. They patch Deep Agents creation with small fake
agents while still exercising the real ``run_agent_question()`` contract,
visible tool filtering, scoped tool wrappers, services, document generation,
and sanitized JSONL tracing.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from langchain_core.messages import AIMessage

import backend.agent.runner as agent_runner
import backend.agent.tracing as agent_tracing
from backend.scripts.ingest_data import run_ingestion


REPO_ROOT = Path(__file__).resolve().parents[1]
MARCH_START = "2026-03-01T00:00:00Z"
APRIL_START = "2026-04-01T00:00:00Z"
FORBIDDEN_TRACE_TOKENS = (
    "company_id",
    "user_id",
    "database_path",
    "filesystem_path",
    "file_path",
    "storage_key",
    "api_key",
    "OPENAI_API_KEY",
    "sqlite",
    "select ",
)


class MockedDeepAgentsOrchestrationEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.database = root / "mocked-orchestration-eval.db"
        cls.storage = root / "documents"
        cls.trace_file = root / "logs" / "agent_traces.jsonl"
        cls.trace_patcher = patch.object(
            agent_tracing, "TRACE_FILE_PATH", cls.trace_file
        )
        cls.trace_patcher.start()
        run_ingestion(REPO_ROOT / "data", cls.database)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.trace_patcher.stop()
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.trace_file.unlink(missing_ok=True)

    def test_mocked_deep_agent_list_plants_scores_trace_and_output(self) -> None:
        answer, fake_agent = self._run_mocked_agent(
            "list_plants",
            "company_1_operator",
            "List my plants",
        )

        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ENERGY_SCOPED_TOOL_NAMES
        )
        self.assertIn("1001", answer)
        self.assertIn("1002", answer)
        self.assertNotIn("2001", answer)
        self.assertNotIn("Plant C2-", answer)
        self._assert_trace_scores(
            expected_tool="list_plants",
            expected_status="success",
            expected_arguments={},
        )

    def test_mocked_deep_agent_energy_question_scores_tool_arguments(self) -> None:
        answer, fake_agent = self._run_mocked_agent(
            "plant_1001_march_energy",
            "company_1_operator",
            "How much energy did plant 1001 produce in March?",
        )

        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ENERGY_SCOPED_TOOL_NAMES
        )
        self.assertIn("185158.85800000", answer)
        self.assertIn("744", answer)
        self._assert_trace_scores(
            expected_tool="get_energy_summary",
            expected_status="success",
            expected_arguments={
                "start": MARCH_START,
                "end": APRIL_START,
                "plant_id": 1001,
            },
        )

    def test_mocked_deep_agent_cross_tenant_lookup_scores_not_found(self) -> None:
        answer, fake_agent = self._run_mocked_agent(
            "cross_tenant_plant",
            "company_1_operator",
            "How much energy did plant 2001 produce in March?",
        )

        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ENERGY_SCOPED_TOOL_NAMES
        )
        self.assertIn("not found or not accessible", answer)
        self.assertNotIn("company_2", answer)
        self.assertNotIn("Plant C2-", answer)
        self._assert_trace_scores(
            expected_tool="get_energy_summary",
            expected_status="not_found",
            expected_arguments={
                "start": MARCH_START,
                "end": APRIL_START,
                "plant_id": 2001,
            },
        )

    def test_mocked_deep_agent_operator_financial_denial_has_no_tool_call(
        self,
    ) -> None:
        answer, fake_agent = self._run_mocked_agent(
            "operator_financial_denial",
            "company_1_operator",
            "Show monthly costs",
        )

        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ENERGY_SCOPED_TOOL_NAMES
        )
        self.assertNotIn("get_monthly_cost_summary", fake_agent.visible_tool_names)
        self.assertNotIn("get_market_price_summary", fake_agent.visible_tool_names)
        self.assertNotIn("create_financial_report", fake_agent.visible_tool_names)
        self.assertIn("Financial access is not permitted", answer)
        events = self._trace_events()
        self.assertFalse(
            any(event["event"] == "agent_tool_call" for event in events)
        )
        self.assertEqual(events[-1]["event"], "agent_run_completed")
        self._assert_trace_redacted(events)

    def test_mocked_deep_agent_admin_report_creation_scores_document_tool(
        self,
    ) -> None:
        answer, fake_agent = self._run_mocked_agent(
            "admin_financial_report",
            "company_1_admin",
            "Generate a PDF financial report for March",
        )

        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ALL_SCOPED_TOOL_NAMES
        )
        self.assertIn("/documents/", answer)
        self.assertIn("run_id=", answer)
        self.assertNotIn(str(self.storage), answer)
        self._assert_trace_scores(
            expected_tool="create_financial_report",
            expected_status="success",
            expected_arguments={"format": "pdf", "month": 3},
        )

    def _run_mocked_agent(
        self, scenario: str, user_identifier: str, question: str
    ) -> tuple[str, "_ScenarioDeepAgent"]:
        created_agents: list[_ScenarioDeepAgent] = []

        def fake_create_deep_agent(
            model: object,
            tools: list[Any],
            **_: object,
        ) -> "_ScenarioDeepAgent":
            fake_agent = _ScenarioDeepAgent(scenario, tools)
            created_agents.append(fake_agent)
            return fake_agent

        with patch.dict(
            os.environ, {"OPENAI_API_KEY": "mocked-runtime-key"}, clear=False
        ), patch.object(
            agent_runner, "ChatOpenAI", return_value=object()
        ), patch.object(
            agent_runner, "create_deep_agent", side_effect=fake_create_deep_agent
        ):
            answer = agent_runner.run_agent_question(
                user_identifier,
                question,
                database_path=self.database,
                document_storage_path=self.storage,
            )

        self.assertEqual(len(created_agents), 1)
        return answer, created_agents[0]

    def _assert_trace_scores(
        self,
        *,
        expected_tool: str,
        expected_status: str,
        expected_arguments: Mapping[str, Any],
    ) -> None:
        events = self._trace_events()
        tool_events = [
            event for event in events if event["event"] == "agent_tool_call"
        ]
        self.assertEqual(len(tool_events), 1)
        tool_event = tool_events[0]
        self.assertEqual(tool_event["tool"], expected_tool)
        self.assertEqual(tool_event["status"], expected_status)
        self.assertEqual(tool_event["arguments"], dict(expected_arguments))
        self.assertIn("timestamp", tool_event)
        self.assertGreaterEqual(tool_event["duration_ms"], 0)
        self.assertEqual(events[-1]["event"], "agent_run_completed")
        self.assertGreaterEqual(events[-1]["total_duration_ms"], 0)
        self._assert_trace_redacted(events)

    def _assert_trace_redacted(self, events: list[dict]) -> None:
        serialized = json.dumps(events)
        self.assertNotIn("mocked-runtime-key", serialized)
        self.assertNotIn(str(self.database), serialized)
        self.assertNotIn(str(self.storage), serialized)
        for token in FORBIDDEN_TRACE_TOKENS:
            self.assertNotIn(token, serialized)

    def _trace_events(self) -> list[dict]:
        self.assertTrue(self.trace_file.exists())
        return [
            json.loads(line)
            for line in self.trace_file.read_text(encoding="utf-8").splitlines()
        ]


class _ScenarioDeepAgent:
    def __init__(self, scenario: str, tools: list[Any]) -> None:
        self.scenario = scenario
        self.tools = {tool.name: tool for tool in tools}
        self.visible_tool_names = [tool.name for tool in tools]

    def invoke(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.scenario == "list_plants":
            result = self._call("list_plants", {})
            plant_ids = [
                str(plant["plant_id"]) for plant in result["plants"]
            ]
            return self._answer(f"Accessible plants: {', '.join(plant_ids)}")

        if self.scenario == "plant_1001_march_energy":
            result = self._call(
                "get_energy_summary",
                {
                    "start": MARCH_START,
                    "end": APRIL_START,
                    "plant_id": 1001,
                },
            )
            metric = next(
                item
                for item in result["plant_summaries"][0]["metrics"]
                if item["datasource_id"] == 100101
            )
            return self._answer(
                "Plant 1001 produced "
                f"{metric['value']} kWh from {metric['sample_count']} readings."
            )

        if self.scenario == "cross_tenant_plant":
            result = self._call(
                "get_energy_summary",
                {
                    "start": MARCH_START,
                    "end": APRIL_START,
                    "plant_id": 2001,
                },
            )
            return self._answer(result["error"])

        if self.scenario == "operator_financial_denial":
            if "get_monthly_cost_summary" in self.tools:
                raise AssertionError("financial tool should not be visible")
            return self._answer("Financial access is not permitted.")

        if self.scenario == "admin_financial_report":
            result = self._call(
                "create_financial_report", {"format": "pdf", "month": 3}
            )
            document = result["document"]
            return self._answer(
                f"Report ready: {document['download_url']} "
                f"({document['filename']})"
            )

        raise AssertionError(f"unsupported mocked scenario: {self.scenario}")

    def _call(self, tool_name: str, arguments: Mapping[str, Any]) -> dict:
        return json.loads(self.tools[tool_name].invoke(dict(arguments)))

    @staticmethod
    def _answer(content: str) -> Mapping[str, Any]:
        return {"messages": [AIMessage(content=content)]}


if __name__ == "__main__":
    unittest.main()
