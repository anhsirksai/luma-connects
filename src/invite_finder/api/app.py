from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from invite_finder import db
from invite_finder.api.deps import get_settings
from invite_finder.api.routes_chat import router as chat_router
from invite_finder.api.routes_events import router as events_router
from invite_finder.api.routes_runs import router as runs_router
from invite_finder.api.routes_webhooks import router as webhooks_router
from invite_finder.api.schemas import HealthResponse
from invite_finder.brightdata import BrightDataError
from invite_finder.cache import CacheMiss
from invite_finder.config import ConfigError, Settings
from invite_finder.luma import LumaParseError, LumaUrlError
from invite_finder.store import commerce_store, run_store


HTTP_STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    502: "upstream_error",
    503: "unavailable",
}


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "detail": None}},
    )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(settings.invite_db_path)
        try:
            reaped = run_store.reap_stuck_runs(conn)
            if reaped:
                print(f"invite-api: marked {reaped} interrupted run(s) as failed on startup")
        finally:
            conn.close()
        yield

    app = FastAPI(title="Luma Connects API", version="0.1.0", lifespan=lifespan)

    origins = [o.strip() for o in settings.invite_cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(events_router)
    app.include_router(runs_router)
    app.include_router(chat_router)
    app.include_router(webhooks_router)

    @app.exception_handler(LumaUrlError)
    async def _luma_url_error(request: Request, exc: LumaUrlError) -> JSONResponse:
        return _error_response(400, "invalid_luma_url", str(exc))

    @app.exception_handler(LumaParseError)
    async def _luma_parse_error(request: Request, exc: LumaParseError) -> JSONResponse:
        return _error_response(502, "luma_parse_failed", str(exc))

    @app.exception_handler(BrightDataError)
    async def _brightdata_error(request: Request, exc: BrightDataError) -> JSONResponse:
        return _error_response(502, "brightdata_error", str(exc))

    @app.exception_handler(CacheMiss)
    async def _cache_miss(request: Request, exc: CacheMiss) -> JSONResponse:
        return _error_response(503, "cache_miss_offline", str(exc))

    @app.exception_handler(ConfigError)
    async def _config_error(request: Request, exc: ConfigError) -> JSONResponse:
        return _error_response(503, "config_error", str(exc))

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        # Normalize FastAPI's default {"detail": ...} shape (raised via plain
        # `raise HTTPException(...)` across the routes) to the same
        # {"error": {code, message}} envelope every other error uses.
        code = HTTP_STATUS_CODES.get(exc.status_code, "http_error")
        return _error_response(exc.status_code, code, str(exc.detail))

    @app.get("/api/health", response_model=HealthResponse)
    def health(request_settings: Settings = Depends(get_settings)) -> HealthResponse:
        # Resolved via Depends rather than the closed-over `settings` so the
        # health view always reflects the settings actually in force.
        settings = request_settings
        conn = db.connect(settings.invite_db_path)
        unfulfilled_cents = 0
        unfulfilled_count = 0
        try:
            conn.execute("SELECT 1")
            db_status = "ok"
            unfulfilled_cents = commerce_store.unresolved_orphan_cents(conn)
            unfulfilled_count = len(commerce_store.list_unresolved_orphans(conn))
        except Exception:  # noqa: BLE001
            db_status = "error"
        finally:
            conn.close()
        return HealthResponse(
            status="degraded" if unfulfilled_count else "ok",
            db=db_status,
            offline=settings.invite_offline,
            unfulfilled_payments_cents=unfulfilled_cents,
            unfulfilled_payments_count=unfulfilled_count,
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("invite_finder.api.app:app", host="0.0.0.0", port=port, workers=1)


if __name__ == "__main__":
    main()
