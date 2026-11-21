"""Application factory and secure optional static frontend hosting."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api import router
from .storage import RunNotFound, SnapshotError, SnapshotStore


LOGGER = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def create_app(data_dir: str | Path | None = None, frontend_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Nepal Landslide Watch",
        description="Experimental landslide research forecasts with explicit provenance and validity intervals.",
        version="0.1.0",
    )
    app.state.store = SnapshotStore(data_dir or os.environ.get("NEPAL_DATA_DIR", str(REPOSITORY_ROOT / "data")))
    app.state.frontend_dir = Path(frontend_dir or os.environ.get("NEPAL_FRONTEND_DIR", str(REPOSITORY_ROOT / "frontend" / "dist"))).expanduser().resolve()
    origins = [value.strip() for value in os.environ.get("NEPAL_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "OPTIONS"], allow_headers=["Accept", "Content-Type"])
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(SnapshotError)
    async def snapshot_error(request: Request, exc: SnapshotError):
        LOGGER.warning("Snapshot read failed: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "Published data is temporarily unavailable. No forecast values have been substituted.", "status": "unavailable"}, headers={"Retry-After": "60"})

    @app.exception_handler(RunNotFound)
    async def run_not_found(request: Request, exc: RunNotFound):
        return JSONResponse(status_code=404, content={"detail": "The requested published run does not exist."})

    @app.exception_handler(ValueError)
    async def invalid_selector(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": "The request contains an invalid value."})

    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/{requested_path:path}", include_in_schema=False)
    def frontend(requested_path: str):
        if requested_path == "api" or requested_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found.")
        root = app.state.frontend_dir
        # Reject hidden paths and traversal before attempting SPA fallback.
        if "\\" in requested_path or any(part.startswith(".") for part in Path(requested_path).parts):
            raise HTTPException(status_code=404, detail="File not found.")
        candidate = (root / requested_path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=404, detail="File not found.")
        if candidate.is_file():
            cache = "public, max-age=31536000, immutable" if requested_path.startswith("assets/") else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        # Missing JS/CSS/image files must not become successful HTML responses.
        if Path(requested_path).suffix or requested_path.startswith("assets/"):
            raise HTTPException(status_code=404, detail="File not found.")
        index = root / "index.html"
        if index.is_file() and index.resolve().is_relative_to(root):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(status_code=503, content={"service": "Nepal Landslide Watch", "status": "frontend_not_built", "message": "Build the frontend to view the dashboard. The read-only API is available at /api/status."})

    return app


app = create_app()
