"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import routes, ws
from .config import Settings, get_settings
from .datasources.registry import DataRegistry
from .db import Database
from .events import EventBus
from .llm.factory import build_providers
from .llm.router import LLMRouter
from .service import AnalysisService

log = logging.getLogger("app")


def create_app(settings: Settings | None = None, *, http_transport: httpx.AsyncBaseTransport | None = None,
               llm_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.db_path)
        orphans = db.fail_orphans()
        if orphans:
            log.warning("marked %d interrupted analyses as failed", orphans)
        router = LLMRouter(build_providers(settings, transport=llm_transport), settings, recorder=db.add_llm_call)
        service = AnalysisService(settings, db, DataRegistry(settings, transport=http_transport), router, EventBus())
        app.state.service, app.state.settings = service, settings
        log.info("started: live_data=%s providers=%s", settings.live_data, [p["name"] for p in router.status() if p["configured"]])
        yield
        await service.shutdown()
        db.close()

    app = FastAPI(title="Agentic Policy Data Analytics Platform", version=__version__, lifespan=lifespan,
                  description="Multi-agent analysis of Singapore labour-market and economic data with multi-cloud LLM fallback.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        problems = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "Invalid request", "details": problems}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"error": {"code": "internal_error", "message": "Unexpected server error"}}, status_code=500)

    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app()
