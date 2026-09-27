"""Local FastAPI engine API (docs/07 §1). Bound to 127.0.0.1 only.

`/health`, the Host-header and CORS guards live here; job routes are in
`routes_jobs`, slip + alias routes in `routes_slips`, settled-leg calibration
in `routes_performance`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine.api import routes_jobs, routes_performance, routes_slips
from engine.api.routes_jobs import JobRunner
from engine.config import Settings, get_settings
from engine.db.queries import latest_gate_passed
from engine.db.session import session_scope

DASHBOARD_ORIGIN = "http://localhost:3000"


def create_app(settings: Settings | None = None, db_path: Path | None = None) -> FastAPI:
    """`db_path` overrides the configured DB (tests); None uses settings.db_file."""
    settings = settings or get_settings()
    port = settings.api.port
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    app = FastAPI(title="BetPredictor engine", docs_url="/docs", redoc_url=None)
    app.state.runner = JobRunner(settings, db_path)
    app.include_router(routes_jobs.router)
    app.include_router(routes_slips.router)
    app.include_router(routes_performance.router)
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
        with session_scope(db_path) as s:
            gate = latest_gate_passed(s)
        return {
            "ok": True,
            "db_path": str(db_path or settings.db_file),
            "model_version": settings.model.version,
            "gate_passed": gate,
            "job_running": app.state.runner.busy,
        }

    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.api.host, port=settings.api.port)


if __name__ == "__main__":
    run()
