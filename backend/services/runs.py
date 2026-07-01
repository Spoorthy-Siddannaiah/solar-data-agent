"""Owned synchronous run persistence and lookup."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import UserContext
from backend.models import AgentRun, GeneratedDocument
from backend.services.documents import GeneratedDocumentInfo
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    run_type: str
    status: str
    result: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    documents: Tuple[GeneratedDocumentInfo, ...]


class RunService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_chat_result(self, ctx: UserContext, answer: str) -> RunInfo:
        now = datetime.now(timezone.utc)
        run = AgentRun(
            run_id=str(uuid.uuid4()),
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            run_type="chat",
            status="completed",
            result_text=answer,
            created_at=now,
            completed_at=now,
        )
        self._session.add(run)
        self._session.flush()
        return self.get_run(ctx, run.run_id)

    def get_run(self, ctx: UserContext, run_id: str) -> RunInfo:
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.run_id == run_id,
                AgentRun.company_id == ctx.company_id,
                AgentRun.user_id == ctx.user_id,
            )
        )
        if run is None:
            raise ResourceNotFoundError("run not found or not accessible")
        documents = tuple(
            self._session.scalars(
                select(GeneratedDocument)
                .where(
                    GeneratedDocument.company_id == ctx.company_id,
                    GeneratedDocument.user_id == ctx.user_id,
                    GeneratedDocument.run_id == run_id,
                )
                .order_by(GeneratedDocument.created_at)
            )
        )
        if any(
            document.classification == "financial" for document in documents
        ) and not ctx.can_view_financials:
            raise PermissionDeniedError("financial data access is not permitted")
        return RunInfo(
            run_id=run.run_id,
            run_type=run.run_type,
            status=run.status,
            result=run.result_text,
            created_at=run.created_at,
            completed_at=run.completed_at,
            documents=tuple(_document_info(document) for document in documents),
        )


def _document_info(document: GeneratedDocument) -> GeneratedDocumentInfo:
    return GeneratedDocumentInfo(
        document_id=document.document_id,
        run_id=document.run_id,
        filename=document.filename,
        media_type=document.media_type,
        byte_size=document.byte_size,
        checksum=document.checksum,
        classification=document.classification,
        created_at=document.created_at,
    )
