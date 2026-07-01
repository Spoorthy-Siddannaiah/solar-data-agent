"""Security-boundary tests for model-callable scoped tools."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

import backend.agent.runner as agent_runner
import backend.agent.tracing as agent_tracing
import deepagents.graph
from langchain_core.messages import AIMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
from sqlalchemy import select

from backend.agent.tools import build_scoped_tools, model_visible_tools
from backend.auth import load_user_context
from backend.database import create_session_factory, create_sqlite_engine
from backend.models import GeneratedDocument
from backend.scripts.ingest_data import run_ingestion
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = {
    "list_plants",
    "get_energy_summary",
    "compare_plants",
    "get_market_price_summary",
    "get_monthly_cost_summary",
    "create_energy_report",
    "create_financial_report",
}
FORBIDDEN_SCHEMA_KEYS = {
    "company_id",
    "user_id",
    "sql",
    "table",
    "table_name",
    "database",
    "database_path",
    "filesystem_path",
    "file_path",
}


class ScopedAgentToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.database = Path(cls.temp_dir.name) / "agent-tools.db"
        cls.storage = Path(cls.temp_dir.name) / "storage"
        cls.trace_file = Path(cls.temp_dir.name) / "logs" / "agent_traces.jsonl"
        cls.trace_patcher = patch.object(
            agent_tracing, "TRACE_FILE_PATH", cls.trace_file
        )
        cls.trace_patcher.start()
        run_ingestion(REPO_ROOT / "data", cls.database)
        cls.engine = create_sqlite_engine(cls.database)
        cls.session_factory = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls.trace_patcher.stop()
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.trace_file.unlink(missing_ok=True)

    def test_tool_schemas_do_not_expose_forbidden_scope_inputs(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            tools = build_scoped_tools(session, ctx)
            schemas = [tool.openai_schema() for tool in tools]

        self.assertEqual({schema["name"] for schema in schemas}, EXPECTED_TOOL_NAMES)
        for schema in schemas:
            with self.subTest(tool=schema["name"]):
                keys = self._all_keys(schema["parameters"])
                self.assertTrue(keys.isdisjoint(FORBIDDEN_SCHEMA_KEYS))

    def test_no_raw_sql_or_database_tool_exists(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            tools = build_scoped_tools(session, ctx)

        names = {tool.name.lower() for tool in tools}
        self.assertEqual(names, EXPECTED_TOOL_NAMES)
        self.assertFalse(
            any(token in name for name in names for token in ("sql", "database", "query"))
        )

    def test_company_1_list_tool_returns_only_company_1_plants(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            tools = {tool.name: tool for tool in build_scoped_tools(session, ctx)}
            result = json.loads(tools["list_plants"].invoke({}))

        plant_ids = [plant["plant_id"] for plant in result["plants"]]
        self.assertEqual(plant_ids, [1001, 1002])
        self.assertNotIn("company_id", json.dumps(result))
        self.assertNotIn("Plant C2-", json.dumps(result))

    def test_energy_only_user_cannot_invoke_financial_tools(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            tools = {tool.name: tool for tool in build_scoped_tools(session, ctx)}
            with self.assertRaises(PermissionDeniedError):
                tools["get_market_price_summary"].invoke(
                    {"start": None, "end": None}
                )
            with self.assertRaises(PermissionDeniedError):
                tools["get_monthly_cost_summary"].invoke({"month": None})

    def test_energy_only_model_does_not_receive_financial_tools(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            tools = model_visible_tools(build_scoped_tools(session, ctx), ctx)

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "list_plants",
                "get_energy_summary",
                "compare_plants",
                "create_energy_report",
            },
        )

    def test_deep_agent_visible_tool_names_are_exact(self) -> None:
        with patch.object(agent_runner, "DATABASE_PATH", self.database), patch.object(
            agent_runner, "DOCUMENT_STORAGE_PATH", self.storage
        ):
            admin = agent_runner._load_context("company_1_admin")
            operator = agent_runner._load_context("company_1_operator")
            admin_names = tuple(
                tool.name for tool in agent_runner._build_langchain_tools(admin)
            )
            admin_tools = agent_runner._build_langchain_tools(admin)
            operator_names = tuple(
                tool.name for tool in agent_runner._build_langchain_tools(operator)
            )

        self.assertEqual(admin_names, agent_runner.ALL_SCOPED_TOOL_NAMES)
        self.assertEqual(operator_names, agent_runner.ENERGY_SCOPED_TOOL_NAMES)
        self.assertTrue(
            set(admin_names).isdisjoint(agent_runner.BROAD_DEEP_AGENT_TOOL_NAMES)
        )
        for tool in admin_tools:
            with self.subTest(tool=tool.name):
                self.assertTrue(
                    self._all_keys(tool.args_schema).isdisjoint(
                        FORBIDDEN_SCHEMA_KEYS
                    )
                )

    def test_deep_agent_profile_excludes_broad_tools_and_subagents(self) -> None:
        with patch.object(agent_runner, "_PROFILE_REGISTERED", False), patch.object(
            agent_runner, "register_harness_profile"
        ) as register:
            agent_runner._register_safe_harness_profile()

        key, profile = register.call_args.args
        self.assertEqual(key, "openai:gpt-5")
        self.assertEqual(
            profile.excluded_tools, agent_runner.BROAD_DEEP_AGENT_TOOL_NAMES
        )
        self.assertFalse(profile.general_purpose_subagent.enabled)

    def test_deep_agent_middleware_sends_only_exact_scoped_tools_to_model(
        self,
    ) -> None:
        with patch.object(agent_runner, "DATABASE_PATH", self.database), patch.object(
            agent_runner, "DOCUMENT_STORAGE_PATH", self.storage
        ):
            ctx = agent_runner._load_context("company_1_admin")
            scoped_tools = agent_runner._build_langchain_tools(ctx)
        profile = agent_runner.HarnessProfile(
            excluded_tools=agent_runner.BROAD_DEEP_AGENT_TOOL_NAMES,
            general_purpose_subagent=agent_runner.GeneralPurposeSubagentProfile(
                enabled=False
            ),
        )
        model = _CapturingChatModel()
        with patch.object(
            deepagents.graph, "_harness_profile_for_model", return_value=profile
        ):
            graph = agent_runner.create_deep_agent(
                model=model,
                tools=scoped_tools,
                backend=agent_runner.StateBackend(),
                subagents=[],
            )
            graph.invoke({"messages": [{"role": "user", "content": "hello"}]})

        self.assertEqual(
            tuple(model.visible_tool_names), agent_runner.ALL_SCOPED_TOOL_NAMES
        )

    def test_financial_user_can_invoke_financial_tools(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            tools = {tool.name: tool for tool in build_scoped_tools(session, ctx)}
            prices = json.loads(
                tools["get_market_price_summary"].invoke(
                    {"start": None, "end": None}
                )
            )
            costs = json.loads(
                tools["get_monthly_cost_summary"].invoke({"month": 3})
            )

        self.assertTrue(prices["market_price_summaries"])
        self.assertTrue(costs["monthly_cost_summaries"])

    def test_cross_company_plant_id_remains_inaccessible(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            tools = {tool.name: tool for tool in build_scoped_tools(session, ctx)}
            with self.assertRaises(ResourceNotFoundError):
                tools["get_energy_summary"].invoke(
                    {
                        "start": None,
                        "end": None,
                        "plant_id": 2001,
                    }
                )

    def test_admin_report_tool_returns_safe_download_metadata(self) -> None:
        with self.session_factory.begin() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            tools = {
                tool.name: tool
                for tool in build_scoped_tools(session, ctx, self.storage)
            }
            result = json.loads(
                tools["create_energy_report"].invoke(
                    {"format": "pdf", "start": None, "end": None}
                )
            )

        document = result["document"]
        self.assertTrue(document["download_url"].startswith("/documents/"))
        serialized = json.dumps(document)
        for forbidden in (
            "company_id",
            "user_id",
            "storage_key",
            "storage_root",
            "filesystem_path",
            str(self.storage),
        ):
            self.assertNotIn(forbidden, serialized)

    def test_operator_financial_report_tool_is_denied(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_operator")
            tools = {
                tool.name: tool
                for tool in build_scoped_tools(session, ctx, self.storage)
            }
            with self.assertRaises(PermissionDeniedError):
                tools["create_financial_report"].invoke(
                    {"format": "xlsx", "month": 3}
                )

    def test_admin_can_create_report_from_agent_chat(self) -> None:
        fake_agent = _ReportDeepAgent()
        with self.assertLogs("uvicorn.error.agent_tools", level="INFO") as captured:
            with patch.object(
                agent_runner, "DATABASE_PATH", self.database
            ), patch.object(
                agent_runner, "DOCUMENT_STORAGE_PATH", self.storage
            ), patch.object(
                agent_runner, "create_deep_agent", return_value=fake_agent
            ):
                answer = agent_runner.run_agent_question(
                    "company_1_admin", "Create a PDF energy report"
                )

        self.assertIn("/documents/", answer)
        self.assertNotIn("storage", answer)
        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ALL_SCOPED_TOOL_NAMES
        )
        logged_events = [
            json.loads(record.getMessage())
            for record in captured.records
            if record.getMessage().startswith("{")
        ]
        trace = next(
            event
            for event in logged_events
            if event["event"] == "agent_tool_call"
        )
        self.assertEqual(trace["event"], "agent_tool_call")
        self.assertEqual(trace["tool"], "create_energy_report")
        self.assertEqual(trace["arguments"]["format"], "pdf")
        self.assertEqual(trace["status"], "success")
        self.assertGreaterEqual(trace["duration_ms"], 0)
        self.assertIn("timestamp", trace)
        serialized_trace = json.dumps(trace)
        for forbidden in (
            "company_id",
            "user_id",
            "storage_key",
            "api_key",
            str(self.storage),
        ):
            self.assertNotIn(forbidden, serialized_trace)
        document_id = answer.split("/documents/", 1)[1].split("/", 1)[0]
        with self.session_factory() as session:
            persisted = session.scalar(
                select(GeneratedDocument).where(
                    GeneratedDocument.document_id == document_id
                )
            )
        self.assertIsNotNone(persisted)
        file_events = self._trace_events()
        self.assertEqual(file_events[-1]["event"], "agent_run_completed")
        self.assertGreaterEqual(file_events[-1]["total_duration_ms"], 0)
        self.assertTrue(
            any(event["event"] == "agent_tool_call" for event in file_events)
        )

    def test_operator_financial_report_is_denied_from_agent_chat(self) -> None:
        fake_agent = _DeniedFinancialDeepAgent()
        with patch.object(agent_runner, "DATABASE_PATH", self.database), patch.object(
            agent_runner, "DOCUMENT_STORAGE_PATH", self.storage
        ), patch.object(
            agent_runner, "create_deep_agent", return_value=fake_agent
        ):
            answer = agent_runner.run_agent_question(
                "company_1_operator", "Create an Excel financial report"
            )

        self.assertEqual(answer, "Financial access is not permitted.")
        self.assertEqual(
            tuple(fake_agent.visible_tool_names), agent_runner.ENERGY_SCOPED_TOOL_NAMES
        )

    def test_trace_argument_allowlist_redacts_sensitive_values(self) -> None:
        with self.session_factory() as session:
            ctx = load_user_context(session, user_id="company_1_admin")
            tool = {
                item.name: item for item in build_scoped_tools(session, ctx)
            }["create_energy_report"]
        safe = agent_tracing.safe_trace_arguments(
            tool,
            {
                "format": "pdf",
                "start": None,
                "end": None,
                "company_id": "company_2",
                "user_id": "private-user",
                "storage_key": "/private/path",
                "api_key": "secret",
            },
        )

        self.assertEqual(safe, {"format": "pdf", "start": None, "end": None})
        serialized_trace = json.dumps(safe)
        self.assertNotIn("company_2", serialized_trace)
        self.assertNotIn("private-user", serialized_trace)
        self.assertNotIn("/private/path", serialized_trace)
        self.assertNotIn("secret", serialized_trace)

    def test_failed_agent_run_writes_sanitized_latency_event(self) -> None:
        with patch.object(
            agent_runner, "DATABASE_PATH", self.database
        ), patch.object(
            agent_runner, "create_deep_agent", return_value=_FailingDeepAgent()
        ):
            with self.assertRaises(agent_runner.AgentRunnerError):
                agent_runner.run_agent_question(
                    "company_1_admin", "This prompt must not be traced"
                )

        event = self._trace_events()[-1]
        self.assertEqual(event["event"], "agent_run_failed")
        self.assertEqual(event["exception_class"], "RuntimeError")
        self.assertEqual(event["error"], "agent request failed")
        self.assertGreaterEqual(event["total_duration_ms"], 0)
        serialized = json.dumps(event)
        self.assertNotIn("company_1_admin", serialized)
        self.assertNotIn("This prompt must not be traced", serialized)

    def test_openai_client_uses_runtime_environment_key(self) -> None:
        runtime_key = "runtime-key-test-value"
        captured = {}

        def capture_model(**kwargs: Any) -> object:
            captured.update(kwargs)
            return object()

        with patch.dict(os.environ, {"OPENAI_API_KEY": runtime_key}), patch.object(
            agent_runner, "DATABASE_PATH", self.database
        ), patch.object(
            agent_runner, "DOCUMENT_STORAGE_PATH", self.storage
        ), patch.object(
            agent_runner, "ChatOpenAI", side_effect=capture_model
        ), patch.object(
            agent_runner,
            "create_deep_agent",
            return_value=_DeniedFinancialDeepAgent(),
        ):
            answer = agent_runner.run_agent_question(
                "company_1_operator", "What can you do?"
            )

        self.assertEqual(answer, "Financial access is not permitted.")
        self.assertEqual(captured["api_key"], runtime_key)
        self.assertNotIn(runtime_key, self.trace_file.read_text(encoding="utf-8"))

    def _trace_events(self) -> list:
        return [
            json.loads(line)
            for line in self.trace_file.read_text(encoding="utf-8").splitlines()
        ]

    @classmethod
    def _all_keys(cls, value: Any) -> set:
        if isinstance(value, dict):
            keys = set(value)
            for item in value.values():
                keys.update(cls._all_keys(item))
            return keys
        if isinstance(value, list):
            keys = set()
            for item in value:
                keys.update(cls._all_keys(item))
            return keys
        return set()


class _ReportDeepAgent:
    def __init__(self) -> None:
        self.visible_tool_names: list[str] = []

    def invoke(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        tools = agent_runner._build_langchain_tools(
            agent_runner._load_context("company_1_admin")
        )
        self.visible_tool_names = [tool.name for tool in tools]
        report_tool = next(
            tool for tool in tools if tool.name == "create_energy_report"
        )
        output = json.loads(
            report_tool.invoke({"format": "pdf", "start": None, "end": None})
        )
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Report ready: "
                        f"{output['document']['download_url']}"
                    )
                )
            ]
        }


class _DeniedFinancialDeepAgent:
    def __init__(self) -> None:
        self.visible_tool_names: list[str] = []

    def invoke(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        tools = agent_runner._build_langchain_tools(
            agent_runner._load_context("company_1_operator")
        )
        self.visible_tool_names = [tool.name for tool in tools]
        return {
            "messages": [AIMessage(content="Financial access is not permitted.")]
        }


class _FailingDeepAgent:
    def invoke(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("synthetic failure with no sensitive data")


class _CapturingChatModel(BaseChatModel):
    _visible_tool_names: list[str] = PrivateAttr(default_factory=list)

    @property
    def visible_tool_names(self) -> list[str]:
        return self._visible_tool_names

    @property
    def _llm_type(self) -> str:
        return "capturing-test-model"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> BaseChatModel:
        self._visible_tool_names = [tool.name for tool in tools]
        return self

    def _generate(
        self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="direct answer"))]
        )


if __name__ == "__main__":
    unittest.main()
