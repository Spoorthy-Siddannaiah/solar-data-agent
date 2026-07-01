"""Thin FastAPI routes over trusted contexts and scoped services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Callable, Generator, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.agent.runner import AgentRunnerError, run_agent_question
from backend.auth import UserContext, UserContextError, load_user_context
from backend.database import create_session_factory, create_sqlite_engine
from backend.models import User
from backend.services.documents import DocumentService
from backend.services.errors import PermissionDeniedError, ResourceNotFoundError
from backend.services.runs import RunService


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = REPO_ROOT / "backend" / "solar_data.db"
DEFAULT_STORAGE = REPO_ROOT / "backend" / "generated_documents"
FRONTEND_ROOT = REPO_ROOT / "frontend"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    run_id: str
    status: str
    answer: str


class EnergyReportRequest(BaseModel):
    format: Literal["xlsx", "pdf", "docx"]
    start: Optional[datetime] = None
    end: Optional[datetime] = None


class FinancialReportRequest(BaseModel):
    format: Literal["xlsx", "pdf", "docx"]
    month: Optional[int] = Field(default=None, ge=1, le=12)


class DocumentResponse(BaseModel):
    document_id: str
    run_id: str
    filename: str
    media_type: str
    byte_size: int
    checksum: str
    classification: str
    created_at: datetime


class RunResponse(BaseModel):
    run_id: str
    run_type: str
    status: str
    result: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    documents: List[DocumentResponse]


class DemoUserResponse(BaseModel):
    user_id: str
    email: str
    role: str
    can_view_energy: bool
    can_view_financials: bool


def create_app(
    database_path: Path = DEFAULT_DATABASE,
    storage_root: Path = DEFAULT_STORAGE,
    agent_runner: Optional[Callable[[str, str], str]] = None,
) -> FastAPI:
    engine = create_sqlite_engine(database_path)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="Solar Data Agent API", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.storage_root = storage_root.expanduser().resolve()
    app.state.agent_runner = agent_runner or partial(
        run_agent_question,
        database_path=database_path,
        document_storage_path=storage_root,
    )

    @app.exception_handler(PermissionDeniedError)
    async def permission_error(_: Request, error: PermissionDeniedError):
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @app.exception_handler(ResourceNotFoundError)
    async def not_found_error(_: Request, error: ResourceNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(UserContextError)
    async def user_context_error(_: Request, error: UserContextError):
        return JSONResponse(status_code=401, content={"detail": str(error)})

    @app.exception_handler(AgentRunnerError)
    async def agent_error(_: Request, error: AgentRunnerError):
        return JSONResponse(status_code=502, content={"detail": str(error)})

    def get_session(request: Request) -> Generator[Session, None, None]:
        factory: sessionmaker[Session] = request.app.state.session_factory
        with factory.begin() as session:
            yield session

    def get_demo_identifier(
        x_demo_user: str = Header(
            ...,
            alias="X-Demo-User",
            description="Stored demo user ID or email.",
        )
    ) -> str:
        identifier = x_demo_user.strip()
        if not identifier:
            raise UserContextError("demo user identifier must not be empty")
        return identifier

    def get_user_context(
        identifier: str = Depends(get_demo_identifier),
        session: Session = Depends(get_session),
    ) -> UserContext:
        if "@" in identifier:
            return load_user_context(session, email=identifier)
        return load_user_context(session, user_id=identifier)

    def get_document_service(
        request: Request, session: Session = Depends(get_session)
    ) -> DocumentService:
        return DocumentService(session, request.app.state.storage_root)

    def get_run_service(
        session: Session = Depends(get_session),
    ) -> RunService:
        return RunService(session)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/demo-users", response_model=List[DemoUserResponse])
    def demo_users(session: Session = Depends(get_session)) -> list:
        users = session.scalars(
            select(User).where(User.active.is_(True)).order_by(User.user_id)
        )
        return [
            DemoUserResponse(
                user_id=user.user_id,
                email=user.email,
                role=user.role,
                can_view_energy=user.access_scope
                in {"energy", "energy+financial"},
                can_view_financials=user.access_scope == "energy+financial",
            )
            for user in users
        ]

    @app.post("/chat", response_model=ChatResponse)
    def chat(
        body: ChatRequest,
        request: Request,
        identifier: str = Depends(get_demo_identifier),
        ctx: UserContext = Depends(get_user_context),
        run_service: RunService = Depends(get_run_service),
    ) -> ChatResponse:
        answer = request.app.state.agent_runner(identifier, body.question)
        run = run_service.save_chat_result(ctx, answer)
        return ChatResponse(run_id=run.run_id, status=run.status, answer=answer)

    @app.post("/reports/energy", response_model=DocumentResponse, status_code=201)
    def create_energy_report(
        body: EnergyReportRequest,
        ctx: UserContext = Depends(get_user_context),
        service: DocumentService = Depends(get_document_service),
    ) -> dict:
        return asdict(
            service.create_energy_report(
                ctx, body.format, start=body.start, end=body.end
            )
        )

    @app.post(
        "/reports/financial", response_model=DocumentResponse, status_code=201
    )
    def create_financial_report(
        body: FinancialReportRequest,
        ctx: UserContext = Depends(get_user_context),
        service: DocumentService = Depends(get_document_service),
    ) -> dict:
        return asdict(
            service.create_financial_report(ctx, body.format, month=body.month)
        )

    @app.get("/documents/{document_id}/download")
    def download_document(
        document_id: str,
        run_id: str = Query(...),
        ctx: UserContext = Depends(get_user_context),
        service: DocumentService = Depends(get_document_service),
    ) -> Response:
        downloaded = service.download_document(ctx, document_id, run_id)
        return Response(
            content=downloaded.content,
            media_type=downloaded.info.media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{downloaded.info.filename}"'
                )
            },
        )

    @app.get("/runs/{run_id}", response_model=RunResponse)
    def get_run(
        run_id: str,
        ctx: UserContext = Depends(get_user_context),
        service: RunService = Depends(get_run_service),
    ) -> dict:
        return asdict(service.get_run(ctx, run_id))

    app.mount(
        "/demo",
        StaticFiles(directory=FRONTEND_ROOT, html=True),
        name="frontend-demo",
    )

    return app


app = create_app()
