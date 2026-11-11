"""HTTP API (FastAPI). Thin layer: validation + error mapping; all logic lives in service.py.

    POST /api/chat   {message, session_id?} -> answer, sources, answerable, cached, via, session_id, stats
    GET  /api/stats  -> live counters (total_questions / api_calls / cache_hits + extras)
    GET  /health     -> liveness (used by the host's health check)
    GET  /           -> static chat page (static/index.html)

State (cache, counters, chat history) is in memory, so run ONE worker process.
"""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .docs import load_docs
from .llm import GeminiClient, LLMError, LLMResult, Turn
from .service import HelpdeskError, HelpdeskService, TooFast

log = logging.getLogger("helpdesk")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# LLMError.kind -> (HTTP status, Retry-After seconds or None)
LLM_ERROR_HTTP = {
    "rate_limited": (429, 30),
    "unavailable": (503, 10),
    "bad_response": (502, None),
    "config": (500, None),
}


class ChatRequest(BaseModel):
    # Generous hard cap here; the service enforces the friendly 500-char limit.
    message: str = Field(..., max_length=5000)
    session_id: str | None = Field(default=None, max_length=200)


class _Unconfigured:
    """Used when GEMINI_API_KEY is missing: the app still starts and answers with a friendly error."""

    on_call = None

    async def generate(self, question: str, history: Sequence[Turn] = ()) -> LLMResult:
        raise LLMError("config", "GEMINI_API_KEY is not set")


def build_service() -> tuple[HelpdeskService, dict]:
    settings = get_settings()
    knowledge = load_docs()
    for w in knowledge.warnings:
        log.warning("docs: %s", w)
    configured = bool(settings.gemini_api_key)
    if configured:
        llm = GeminiClient(
            settings.gemini_api_key,
            settings.gemini_model,
            knowledge,
            history_turns=settings.history_turns,
        )
    else:
        log.error("GEMINI_API_KEY is not set - chat will return a configuration error")
        llm = _Unconfigured()
    service = HelpdeskService(
        llm,
        knowledge,
        max_concurrent=settings.max_concurrent_llm,
        max_queue=settings.max_queue,
        session_rate_per_min=settings.session_rate_per_min,
        history_turns=settings.history_turns,
        max_question_chars=settings.max_question_chars,
    )
    meta = {
        "configured": configured,
        "model": settings.gemini_model,
        "docs": sorted(knowledge.valid_sources),
    }
    return service, meta


def _error(status: int, kind: str, message: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(
        {"error": kind, "message": message}, status_code=status, headers=headers
    )


def create_app(service: HelpdeskService | None = None, meta: dict | None = None) -> FastAPI:
    """Pass a prepared service in tests; in production it is built once at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "service", None) is None:
            app.state.service, app.state.meta = build_service()
        yield

    app = FastAPI(title="DPM-101 Course Helpdesk", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service
    app.state.meta = meta or {"configured": True, "model": "test", "docs": []}

    # ---------------------------------------------------------------- errors

    @app.exception_handler(HelpdeskError)
    async def _helpdesk_error(request: Request, exc: HelpdeskError):
        retry = exc.retry_after if isinstance(exc, TooFast) else None
        return _error(exc.status, type(exc).__name__.lower(), exc.user_message, retry)

    @app.exception_handler(LLMError)
    async def _llm_error(request: Request, exc: LLMError):
        status, retry = LLM_ERROR_HTTP.get(exc.kind, (503, 10))
        return _error(status, exc.kind, exc.user_message, retry)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, exc: RequestValidationError):
        return _error(400, "badquestion", "Please type a question (up to 500 characters).")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error")
        return _error(500, "internal", "Something went wrong on our side. Please try again.")

    # ---------------------------------------------------------------- routes

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        sid = body.session_id if body.session_id and SESSION_ID_RE.match(body.session_id) else uuid.uuid4().hex
        svc: HelpdeskService = request.app.state.service
        reply = await svc.ask(sid, body.message)
        return JSONResponse(
            {
                "session_id": sid,
                "answer": reply.answer,
                "sources": reply.sources,
                "answerable": reply.answerable,
                "cached": reply.cached,
                "via": reply.via,
                "stats": svc.stats.snapshot(),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/stats")
    async def stats(request: Request):
        return JSONResponse(
            request.app.state.service.stats.snapshot(), headers={"Cache-Control": "no-store"}
        )

    @app.get("/health")
    async def health(request: Request):
        m = request.app.state.meta
        return {"status": "ok", "configured": m["configured"], "model": m["model"], "docs": m["docs"]}

    # Static files last, so they never shadow the API routes.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
