"""Model-callable wrappers around tenant-scoped application services."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from backend.auth import UserContext
from backend.services.finance import FinanceService
from backend.services.documents import DocumentService, GeneratedDocumentInfo
from backend.services.plants import PlantDataService


ToolHandler = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class ScopedAgentTool:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler
    requires_financials: bool = False

    def openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "strict": True,
        }

    def invoke(self, arguments: Mapping[str, Any]) -> str:
        unknown = set(arguments) - set(
            self.parameters.get("properties", {})
        )
        if unknown:
            raise ValueError(f"unsupported tool arguments: {sorted(unknown)}")
        return json.dumps(_jsonable(self.handler(arguments)), sort_keys=True)


def build_scoped_tools(
    session: Session, ctx: UserContext, storage_root: Optional[Path] = None
) -> Tuple[ScopedAgentTool, ...]:
    """Bind narrow tools to one trusted context for one run."""
    plants = PlantDataService(session)
    finance = FinanceService(session)
    documents = DocumentService(
        session,
        storage_root
        if storage_root is not None
        else Path("backend/generated_documents"),
    )

    def list_plants(arguments: Mapping[str, Any]) -> Any:
        return {"plants": plants.list_plants(ctx)}

    def get_energy_summary(arguments: Mapping[str, Any]) -> Any:
        return {
            "plant_summaries": plants.get_energy_summary(
                ctx,
                start=_optional_datetime(arguments.get("start"), "start"),
                end=_optional_datetime(arguments.get("end"), "end"),
                plant_id=_optional_integer(arguments.get("plant_id"), "plant_id"),
            )
        }

    def compare_plants(arguments: Mapping[str, Any]) -> Any:
        return {
            "plant_comparison": plants.compare_plants(
                ctx,
                start=_optional_datetime(arguments.get("start"), "start"),
                end=_optional_datetime(arguments.get("end"), "end"),
                plant_ids=_optional_integer_list(arguments.get("plant_ids")),
            )
        }

    def get_market_price_summary(arguments: Mapping[str, Any]) -> Any:
        summaries = finance.get_market_price_summary(
            ctx,
            start=_optional_datetime(arguments.get("start"), "start"),
            end=_optional_datetime(arguments.get("end"), "end"),
        )
        return {
            "market_price_summaries": [
                {
                    "zone": item.zone,
                    "sample_count": item.sample_count,
                    "minimum_eur_per_mwh": _scaled_decimal(
                        item.minimum_eur_per_mwh_micros, 1_000_000
                    ),
                    "maximum_eur_per_mwh": _scaled_decimal(
                        item.maximum_eur_per_mwh_micros, 1_000_000
                    ),
                    "average_eur_per_mwh": _scaled_decimal(
                        item.average_eur_per_mwh_micros, 1_000_000
                    ),
                }
                for item in summaries
            ]
        }

    def get_monthly_cost_summary(arguments: Mapping[str, Any]) -> Any:
        summaries = finance.get_monthly_cost_summary(
            ctx,
            month=_optional_integer(arguments.get("month"), "month"),
        )
        return {
            "monthly_cost_summaries": [
                {
                    "year": item.year,
                    "month": item.month,
                    "category": item.category,
                    "amount_eur": _scaled_decimal(item.amount_eur_minor, 100),
                }
                for item in summaries
            ]
        }

    def create_energy_report(arguments: Mapping[str, Any]) -> Any:
        info = documents.create_energy_report(
            ctx,
            file_format=_required_format(arguments.get("format")),
            start=_optional_datetime(arguments.get("start"), "start"),
            end=_optional_datetime(arguments.get("end"), "end"),
        )
        return {"document": _safe_document_result(info)}

    def create_financial_report(arguments: Mapping[str, Any]) -> Any:
        info = documents.create_financial_report(
            ctx,
            file_format=_required_format(arguments.get("format")),
            month=_optional_integer(arguments.get("month"), "month"),
        )
        return {"document": _safe_document_result(info)}

    nullable_datetime = {
        "type": ["string", "null"],
        "description": "UTC ISO 8601 timestamp, or null for no bound.",
    }
    return (
        ScopedAgentTool(
            name="list_plants",
            description="List the plants accessible to the current user.",
            parameters=_object_schema({}),
            handler=list_plants,
        ),
        ScopedAgentTool(
            name="get_energy_summary",
            description=(
                "Summarize energy measurements for accessible plants or one "
                "accessible plant over an optional UTC time range."
            ),
            parameters=_object_schema(
                {
                    "start": nullable_datetime,
                    "end": nullable_datetime,
                    "plant_id": {
                        "type": ["integer", "null"],
                        "description": "Plant ID, or null for all accessible plants.",
                    },
                }
            ),
            handler=get_energy_summary,
        ),
        ScopedAgentTool(
            name="compare_plants",
            description=(
                "Compare total energy for accessible plants over an optional "
                "UTC time range."
            ),
            parameters=_object_schema(
                {
                    "start": nullable_datetime,
                    "end": nullable_datetime,
                    "plant_ids": {
                        "type": ["array", "null"],
                        "items": {"type": "integer"},
                        "description": (
                            "Plant IDs to compare, or null for all accessible plants."
                        ),
                    },
                }
            ),
            handler=compare_plants,
        ),
        ScopedAgentTool(
            name="get_market_price_summary",
            description=(
                "Get market price statistics over an optional UTC time range. "
                "Requires financial access."
            ),
            parameters=_object_schema(
                {"start": nullable_datetime, "end": nullable_datetime}
            ),
            handler=get_market_price_summary,
            requires_financials=True,
        ),
        ScopedAgentTool(
            name="get_monthly_cost_summary",
            description=(
                "Get cost totals by category and period. Requires financial access."
            ),
            parameters=_object_schema(
                {
                    "month": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 12,
                        "description": "Month number, or null for all months.",
                    }
                }
            ),
            handler=get_monthly_cost_summary,
            requires_financials=True,
        ),
        ScopedAgentTool(
            name="create_energy_report",
            description=(
                "Create an owned energy report file and return safe document "
                "metadata plus an ownership-checked download URL."
            ),
            parameters=_object_schema(
                {
                    "format": {
                        "type": "string",
                        "enum": ["xlsx", "pdf", "docx"],
                        "description": "Output file format.",
                    },
                    "start": nullable_datetime,
                    "end": nullable_datetime,
                }
            ),
            handler=create_energy_report,
        ),
        ScopedAgentTool(
            name="create_financial_report",
            description=(
                "Create an owned financial report file and return safe document "
                "metadata plus an ownership-checked download URL. Requires "
                "financial access."
            ),
            parameters=_object_schema(
                {
                    "format": {
                        "type": "string",
                        "enum": ["xlsx", "pdf", "docx"],
                        "description": "Output file format.",
                    },
                    "month": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 12,
                        "description": "Month number, or null for all months.",
                    },
                }
            ),
            handler=create_financial_report,
            requires_financials=True,
        ),
    )


def model_visible_tools(
    tools: Sequence[ScopedAgentTool], ctx: UserContext
) -> Tuple[ScopedAgentTool, ...]:
    """Remove financial tools entirely when the context lacks permission."""
    return tuple(
        tool
        for tool in tools
        if not tool.requires_financials or ctx.can_view_financials
    )


def _object_schema(properties: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(properties),
        "additionalProperties": False,
    }


def _optional_datetime(value: Any, label: str) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO 8601 string or null")
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), time(), timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a valid ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_integer(value: Any, label: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer or null")
    return value


def _optional_integer_list(value: Any) -> Optional[Tuple[int, ...]]:
    if value is None:
        return None
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise ValueError("plant_ids must be an array of integers or null")
    return tuple(value)


def _required_format(value: Any) -> str:
    if value not in {"xlsx", "pdf", "docx"}:
        raise ValueError("format must be xlsx, pdf, or docx")
    return value


def _safe_document_result(info: GeneratedDocumentInfo) -> Dict[str, Any]:
    return {
        "document_id": info.document_id,
        "run_id": info.run_id,
        "filename": info.filename,
        "media_type": info.media_type,
        "byte_size": info.byte_size,
        "checksum": info.checksum,
        "classification": info.classification,
        "created_at": info.created_at.isoformat(),
        "download_url": (
            f"/documents/{info.document_id}/download?run_id={info.run_id}"
        ),
    }


def _scaled_decimal(value: int, scale: int) -> str:
    decimal = Decimal(value) / Decimal(scale)
    return format(decimal, "f")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
