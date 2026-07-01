"""LangChain Deep Agents orchestration restricted to scoped service tools."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI

from backend.agent import tracing
from backend.agent.tools import ScopedAgentTool, build_scoped_tools, model_visible_tools
from backend.auth import UserContext, load_user_context
from backend.database import create_session_factory, create_sqlite_engine
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError


REPO_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = REPO_ROOT / "backend" / "solar_data.db"
DOCUMENT_STORAGE_PATH = REPO_ROOT / "backend" / "generated_documents"
MODEL = "gpt-5"
MODEL_SPEC = f"openai:{MODEL}"

ALL_SCOPED_TOOL_NAMES = (
    "list_plants",
    "get_energy_summary",
    "compare_plants",
    "get_market_price_summary",
    "get_monthly_cost_summary",
    "create_energy_report",
    "create_financial_report",
)
ENERGY_SCOPED_TOOL_NAMES = (
    "list_plants",
    "get_energy_summary",
    "compare_plants",
    "create_energy_report",
)
BROAD_DEEP_AGENT_TOOL_NAMES = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)

_PROFILE_LOCK = threading.Lock()
_PROFILE_REGISTERED = False


class AgentRunnerError(RuntimeError):
    """Raised when an agent run cannot safely produce a final answer."""


def run_agent_question(
    user_identifier: str,
    question: str,
    *,
    database_path: Path | None = None,
    document_storage_path: Path | None = None,
) -> str:
    """Answer one question using Deep Agents and tools bound to a stored user."""
    started_at = time.perf_counter()
    try:
        answer = _run_agent_question(
            user_identifier,
            question,
            database_path=database_path or DATABASE_PATH,
            document_storage_path=document_storage_path or DOCUMENT_STORAGE_PATH,
        )
    except Exception:
        tracing.record_agent_run_event(
            "agent_run_failed", tracing.duration_ms(started_at)
        )
        raise
    tracing.record_agent_run_event(
        "agent_run_completed", tracing.duration_ms(started_at)
    )
    return answer


def _run_agent_question(
    user_identifier: str,
    question: str,
    *,
    database_path: Path,
    document_storage_path: Path,
) -> str:
    identifier = user_identifier.strip()
    prompt = question.strip()
    if not identifier:
        raise ValueError("user_identifier must not be empty")
    if not prompt:
        raise ValueError("question must not be empty")
    if not database_path.is_file():
        raise AgentRunnerError(
            "demo database is missing; run the ingestion script first"
        )

    load_dotenv(REPO_ROOT / ".env", override=False)
    if not os.getenv("OPENAI_API_KEY"):
        raise AgentRunnerError("OPENAI_API_KEY is not configured")

    ctx = _load_context(identifier, database_path=database_path)
    tools = _build_langchain_tools(
        ctx,
        database_path=database_path,
        document_storage_path=document_storage_path,
    )
    expected_names = (
        ALL_SCOPED_TOOL_NAMES if ctx.can_view_financials else ENERGY_SCOPED_TOOL_NAMES
    )
    if tuple(tool.name for tool in tools) != expected_names:
        raise AgentRunnerError("unsafe agent tool configuration")

    _register_safe_harness_profile()
    model = ChatOpenAI(
        model=MODEL,
        use_responses_api=True,
        store=False,
        model_kwargs={"parallel_tool_calls": False},
    )
    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=_instructions(ctx.can_view_financials),
        backend=StateBackend(),
        subagents=[],
    )
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    except Exception as error:
        raise AgentRunnerError("agent request failed") from error
    return _extract_final_text(result)


def _load_context(
    identifier: str, *, database_path: Path | None = None
) -> UserContext:
    engine = create_sqlite_engine(database_path or DATABASE_PATH)
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            lookup = (
                {"email": identifier} if "@" in identifier else {"user_id": identifier}
            )
            return load_user_context(session, **lookup)
    finally:
        engine.dispose()


def _build_langchain_tools(
    ctx: UserContext,
    *,
    database_path: Path | None = None,
    document_storage_path: Path | None = None,
) -> tuple[BaseTool, ...]:
    """Build model tools whose handlers open an isolated database session."""
    resolved_database_path = database_path or DATABASE_PATH
    resolved_storage_path = document_storage_path or DOCUMENT_STORAGE_PATH
    engine = create_sqlite_engine(resolved_database_path)
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            definitions = model_visible_tools(
                build_scoped_tools(session, ctx, resolved_storage_path), ctx
            )
    finally:
        engine.dispose()
    return tuple(
        _to_langchain_tool(
            definition,
            ctx,
            database_path=resolved_database_path,
            document_storage_path=resolved_storage_path,
        )
        for definition in definitions
    )


def _to_langchain_tool(
    definition: ScopedAgentTool,
    ctx: UserContext,
    *,
    database_path: Path,
    document_storage_path: Path,
) -> BaseTool:
    def invoke_scoped_tool(**arguments: Any) -> str:
        started_at = time.perf_counter()
        safe_arguments = tracing.safe_trace_arguments(definition, arguments)
        engine = create_sqlite_engine(database_path)
        try:
            session_factory = create_session_factory(engine)
            with session_factory.begin() as session:
                tools = {
                    tool.name: tool
                    for tool in build_scoped_tools(
                        session, ctx, storage_root=document_storage_path
                    )
                }
                result = tools[definition.name].invoke(arguments)
            tracing.record_tool_call(
                definition.name,
                safe_arguments,
                "success",
                tracing.duration_ms(started_at),
            )
            return result
        except PermissionDeniedError:
            tracing.record_tool_call(
                definition.name,
                safe_arguments,
                "permission_denied",
                tracing.duration_ms(started_at),
            )
            return json.dumps({"error": "permission denied"})
        except ResourceNotFoundError:
            tracing.record_tool_call(
                definition.name,
                safe_arguments,
                "not_found",
                tracing.duration_ms(started_at),
            )
            return json.dumps({"error": "resource not found or not accessible"})
        except (TypeError, ValueError):
            tracing.record_tool_call(
                definition.name,
                {},
                "invalid_arguments",
                tracing.duration_ms(started_at),
            )
            return json.dumps({"error": "invalid tool arguments"})
        except Exception:
            tracing.record_tool_call(
                definition.name, {}, "error", tracing.duration_ms(started_at)
            )
            raise
        finally:
            engine.dispose()

    return StructuredTool.from_function(
        func=invoke_scoped_tool,
        name=definition.name,
        description=definition.description,
        args_schema=dict(definition.parameters),
    )


def _register_safe_harness_profile() -> None:
    global _PROFILE_REGISTERED
    with _PROFILE_LOCK:
        if _PROFILE_REGISTERED:
            return
        register_harness_profile(
            MODEL_SPEC,
            HarnessProfile(
                base_system_prompt=(
                    "Use only the explicitly provided scoped solar-data tools."
                ),
                excluded_tools=BROAD_DEEP_AGENT_TOOL_NAMES,
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )
        _PROFILE_REGISTERED = True


def _extract_final_text(result: Mapping[str, Any]) -> str:
    messages: Sequence[Any] = result.get("messages", ())
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        content = message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(part for part in parts if part).strip()
            if text:
                return text
    raise AgentRunnerError("model returned no final text")


def _instructions(can_view_financials: bool) -> str:
    access_instruction = (
        "The current user may access financial summaries."
        if can_view_financials
        else (
            "The current user has energy-only access. Do not provide market "
            "prices, costs, revenue, margin, or profit. If asked, clearly say "
            "financial access is not permitted."
        )
    )
    return (
        "You answer questions about the current user's solar plants. "
        "Use the provided tools for every factual plant-data request; do not "
        "infer or invent values. Answer directly only for greetings, capability "
        "explanations, and permission denials that require no data. For requests "
        "to generate, create, export, or download a report, PDF, Word document, "
        "or Excel workbook, call the matching create report tool. Never claim a "
        "file was created unless the tool succeeds, and include its download_url "
        "in the answer. Never ask for or claim to change tenant identity. "
        "Treat inaccessible resources as not found without revealing whether "
        "they exist elsewhere. The demo data is for 2026; interpret an "
        "unqualified month as that month in 2026. "
        f"{access_instruction}"
    )
