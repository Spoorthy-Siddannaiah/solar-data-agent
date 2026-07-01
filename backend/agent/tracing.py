"""Sanitized, file-backed tracing for agent runs and scoped tool calls."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_FILE_PATH = REPO_ROOT / "backend" / "logs" / "agent_traces.jsonl"
LOGGER = logging.getLogger("uvicorn.error.agent_tools")
TRACE_LOCK = threading.Lock()


def duration_ms(started_at: float) -> float:
    return round(max(0.0, (time.perf_counter() - started_at) * 1000), 3)


def safe_trace_arguments(tool: Any, arguments: Mapping[str, Any]) -> dict:
    """Return bounded values for schema-declared, non-sensitive arguments."""
    safe = {}
    properties = tool.parameters.get("properties", {})
    for key in properties:
        if key not in arguments:
            continue
        value = arguments[key]
        if value is None or isinstance(value, int) and not isinstance(value, bool):
            safe[key] = value
        elif key == "format" and value in {"xlsx", "pdf", "docx"}:
            safe[key] = value
        elif key in {"start", "end"} and isinstance(value, str) and len(value) <= 40:
            safe[key] = value
        elif key == "plant_ids" and isinstance(value, list):
            if len(value) <= 20 and all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in value
            ):
                safe[key] = value
    return safe


def record_tool_call(
    name: str,
    arguments: Mapping[str, Any],
    status: str,
    elapsed_ms: float,
) -> None:
    record_trace_event(
        {
            "event": "agent_tool_call",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": name,
            "arguments": dict(arguments),
            "status": status,
            "duration_ms": elapsed_ms,
        }
    )


def record_agent_run_event(event_name: str, total_duration_ms: float) -> None:
    record_trace_event(
        {
            "event": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_duration_ms": total_duration_ms,
        }
    )


def record_trace_event(event: Mapping[str, Any]) -> None:
    LOGGER.info("%s", json.dumps(event, sort_keys=True))
    line = json.dumps(dict(event), sort_keys=True, separators=(",", ":"))
    try:
        with TRACE_LOCK:
            TRACE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with TRACE_FILE_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
    except OSError:
        LOGGER.warning("agent trace file write failed")
