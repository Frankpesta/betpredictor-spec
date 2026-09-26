"""Local FastAPI engine API (docs/07 §1). Bound to 127.0.0.1 only.

Phase 0 provides `/health` plus the Host-header and CORS guards; job and slip
routes arrive in Phase 7.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from engine.config import get_settings
from engine.db.models import BacktestRun
from engine.db.session import session_scope

DASHBOARD_ORIGIN = "http://localhost:3000"


def create_app() -> FastAPI:
    settings = get_settings()
    port = settings.api.port
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    app = FastAPI(title="BetPredictor engine", docs_url="/docs", redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[DASHBOARD_ORIGIN],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def reject_foreign_hosts(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope() as s:
            latest = s.scalars(
                select(BacktestRun)
                .where(BacktestRun.finished_at.is_not(None))
                .order_by(BacktestRun.finished_at.desc())
                .limit(1)
            ).first()
            gate = None if latest is None else latest.gate_passed
        return {
            "ok": True,
            "db_path": str(settings.db_file),
            "model_version": settings.model.version,
            "gate_passed": gate,
        }

    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.api.host, port=settings.api.port)


if __name__ == "__main__":
    run()
