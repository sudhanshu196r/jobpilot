"""JobPilot — a personal, local-first job search assistant.

Run with:  python -m app   (or: uvicorn app.main:app --port 8765)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import config
from .db import init_db, session_scope
from .routers import api, ext
from .services import settings_store, store

log = logging.getLogger("jobpilot")
STATIC_DIR = Path(__file__).parent / "static"


async def _ingest_loop() -> None:
    """Pull job sources on the configured interval."""
    await asyncio.sleep(20)
    while True:
        with session_scope() as session:
            hours = float(settings_store.get(session, "ingest").get("interval_hours") or 0)
        if hours > 0:
            try:
                def work():
                    with session_scope() as s:
                        return store.run_ingest(s)

                result = await asyncio.to_thread(work)
                log.info("Scheduled ingest: %s", {k: result.get(k) for k in ("new", "fetched", "errors")})
            except Exception:  # keep the loop alive
                log.exception("Scheduled ingest failed")
        await asyncio.sleep(max(hours, 0.25) * 3600)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with session_scope() as session:
        settings_store.extension_token(session)  # make sure one exists
    task = None if config.disable_scheduler else asyncio.create_task(_ingest_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="JobPilot", version="1.0.0", lifespan=lifespan)

# Only the extension may call cross-origin; the UI is same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|moz-extension)://[a-z0-9\-]+$",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-JobPilot-Token", "X-JobPilot-Client"],
)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Block cross-site writes to the local API.

    Browsers cannot attach a custom header cross-origin without a CORS
    preflight, which we only grant to extensions; so requiring one on every
    state-changing request stops a random website from driving this server.
    """
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if not (request.headers.get("x-jobpilot-client") or request.headers.get("x-jobpilot-token")):
            return JSONResponse({"detail": "Missing X-JobPilot-Client header"}, status_code=403)
    return await call_next(request)


app.include_router(api.router)
app.include_router(ext.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
