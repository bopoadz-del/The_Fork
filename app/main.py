"""Cerebrum Blocks - Simple Block Execution API."""

import os
import sys

# Force fresh bytecode on Render deployments (clear stale __pycache__)
for root, dirs, files in os.walk(os.path.dirname(os.path.abspath(__file__))):
    for d in dirs:
        if d == "__pycache__":
            try:
                import shutil
                shutil.rmtree(os.path.join(root, d))
            except Exception:
                pass

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv()

logger = logging.getLogger(__name__)

from app.blocks import BLOCK_REGISTRY
from app.dependencies import block_instances, _create_block_instance, init_blocks
from app.routers import (
    agents as agents_router,
    auth,
    blocks,
    chain,
    chat,
    debug,
    execute,
    health,
    memory,
    mcp,
    monitoring,
    static,
    upload,
)
from app.agents import load_agents
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all blocks + load runtime agents at startup."""
    await init_blocks()
    loaded = load_agents()
    logger.info("Loaded %d runtime agents: %s", len(loaded), ", ".join(sorted(loaded.keys())))
    yield


app = FastAPI(
    title="Cerebrum Blocks",
    description="Build AI Like Lego - Simple Block Execution API",
    version="2.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

_extra_origins = [
    o.strip()
    for o in os.getenv("CORS_EXTRA_ORIGINS", "").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cerebrum-platform-frontend-fork.onrender.com",
        "https://cerebrum-platform-api-fork.onrender.com",
        "http://localhost:3000",
        "http://localhost:4173",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        *_extra_origins,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# File upload security — only intercepts actual upload paths, never chat/chain
@app.middleware("http")
async def file_upload_security_middleware(request: Request, call_next):
    path = request.url.path.lower()

    if "/upload" in path:
        body = await request.body()

        try:
            import json
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if any(k in str(data) for k in ["file_path", "filename", "file"]):
            try:
                if "security" not in block_instances:
                    block_instances["security"] = _create_block_instance(BLOCK_REGISTRY["security"])
                security = block_instances.get("security")
                if security:
                    validation = await security.validate_file(data, {})
                    if not validation.get("safe"):
                        return JSONResponse(
                            status_code=400,
                            content={
                                "status": "error",
                                "error": "Security validation failed",
                                "details": validation.get("error"),
                                "violation": validation.get("violation"),
                            },
                        )
            except Exception:
                pass

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive, request._send)

    return await call_next(request)


# ── Unified error envelope ────────────────────────────────────────────────
# All API errors are returned as:
#   {"error": {"code": "<MACHINE>", "message": "<HUMAN>", "details"?: {...}},
#    "detail": "<HUMAN>"}      # legacy, kept so existing UI code doesn't break.
#
# Status code → code mapping is generic; routes can raise HTTPException with
# detail=<dict> to override (e.g. detail={"code":"INSUFFICIENT_QUOTA","message":...}).

_STATUS_CODE_NAME = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "UNAVAILABLE",
    504: "TIMEOUT",
}


def _envelope(status: int, message: str, code: str | None = None, details=None):
    body = {
        "error": {
            "code": code or _STATUS_CODE_NAME.get(status, "ERROR"),
            "message": message,
        },
        "detail": message,  # legacy field — keep until callers migrate
    }
    if details is not None:
        body["error"]["details"] = details
    return body


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(_request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        msg = str(detail.get("message") or detail.get("error") or detail.get("detail") or "")
        code = str(detail.get("code") or _STATUS_CODE_NAME.get(exc.status_code, "ERROR"))
        rest = {k: v for k, v in detail.items() if k not in {"code", "message", "error", "detail"}}
        body = _envelope(exc.status_code, msg, code, rest or None)
    else:
        body = _envelope(exc.status_code, str(detail))
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None) or None)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_envelope(422, "Request validation failed", "VALIDATION_ERROR", {"errors": exc.errors()}),
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    # Never leak internal stack traces in the response — log them, return generic.
    return JSONResponse(
        status_code=500,
        content=_envelope(500, "Internal server error", "INTERNAL_ERROR"),
    )


# Include all routers
app.include_router(blocks.router)
app.include_router(execute.router)
app.include_router(chain.router)
app.include_router(chat.router)
app.include_router(upload.router)
app.include_router(auth.router)
app.include_router(memory.router)
app.include_router(monitoring.router)
app.include_router(health.router)
app.include_router(mcp.router)
app.include_router(agents_router.router)
app.include_router(static.router)
# Debug routes — only in non-production environments
env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).strip().lower()
if env in {"dev", "development", "local", "test", "testing"}:
    app.include_router(debug.router)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Mount built React dashboard if present (built locally; Render not required)
_dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.isdir(_dashboard_dir) and os.path.exists(os.path.join(_dashboard_dir, "index.html")):
    app.mount(
        "/dashboard",
        StaticFiles(directory=_dashboard_dir, html=True),
        name="dashboard",
    )
    logger.info("Dashboard mounted at /dashboard from %s", _dashboard_dir)
else:
    logger.info("Dashboard not built; skipping /dashboard mount (run: cd frontend && npm install && npm run build)")
