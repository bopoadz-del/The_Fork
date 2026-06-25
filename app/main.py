"""Cerebrum Blocks - Simple Block Execution API."""

import os
import sys

# Force fresh bytecode on deployments (clear stale __pycache__)
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

from app.infra.monitoring import (
    configure_structured_logging,
    init_sentry,
    observability_middleware,
    sentry_enabled,
)

configure_structured_logging()
logger = logging.getLogger(__name__)


_SENTRY_ENABLED = init_sentry()  # noqa: F841 — init side effect only

from app.blocks import BLOCK_REGISTRY
from app.dependencies import block_instances, _create_block_instance, init_blocks
from app.routers import (
    agents as agents_router,
    auth,
    blocks,
    chain,
    chat,
    debug,
    doc_search,
    doc_types,
    drive,
    execute,
    feedback as feedback_router,
    health,
    hydration as hydration_router,
    memory,
    mcp,
    monitoring,
    project,
    projects,
    rag as rag_router,
    redline,
    static,
    upload,
    users,
    workflows,
)
from app.agents import load_agents
def _bootstrap_first_user() -> None:
    """Create a single bootstrap user from env vars if no users exist yet.

    Avoids opening global registration on a public deploy: the operator sets
    BOOTSTRAP_USER_EMAIL + BOOTSTRAP_USER_PASSWORD once on the host, the first
    boot seeds that account, and subsequent boots no-op (idempotent).
    """
    email = os.getenv("BOOTSTRAP_USER_EMAIL", "").strip().lower()
    password = os.getenv("BOOTSTRAP_USER_PASSWORD", "")
    if not email or not password:
        return
    from app.core import users as users_store
    if users_store.get_user_by_email(email) is not None:
        return
    try:
        users_store.create_user(email, password, role="admin")
        logger.info("bootstrap: created first user %s", email)
    except Exception as e:
        # Fail loud — a silent warning lets a broken bootstrap hide. If the
        # only intended admin can't be created (db lock, schema drift, etc),
        # the operator needs to see it in startup logs as an ERROR, not warn.
        logger.error("bootstrap: FAILED to create user %s: %s", email, e, exc_info=True)
        raise


def _validate_startup_env() -> None:
    """Fail fast on missing security config when ENV is explicitly production.

    SECRET_KEY is required: without it the JWT signing secret is generated
    per-process, so tokens are invalidated on every restart and differ across
    scaled instances. DATA_ENCRYPTION_KEY only warns — encryption at rest is
    opt-in.
    """
    env = os.getenv("ENV", os.getenv("ENVIRONMENT", "")).strip().lower()
    if env not in ("prod", "production"):
        return
    if not os.getenv("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is required when ENV=production — without it the JWT "
            "signing secret is regenerated per process, invalidating all "
            "tokens on restart. Set SECRET_KEY in the environment."
        )
    if not os.getenv("DATA_ENCRYPTION_KEY"):
        logger.warning(
            "DATA_ENCRYPTION_KEY is not set — uploaded documents are stored "
            "UNENCRYPTED at rest."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all blocks + load runtime agents at startup."""
    _validate_startup_env()
    await init_blocks()
    from app.core.projects import init_db
    init_db()
    from app.core.users import init_db as init_users_db
    init_users_db()
    _bootstrap_first_user()
    from app.core.agent_memory import init_db as init_agent_memory_db
    init_agent_memory_db()
    from app.core.doc_index import init_db as init_doc_index_db
    init_doc_index_db()
    from app.core.hydration_store import init_db as init_hydration_db
    init_hydration_db()
    from app.core import rate_limit as _rate_limit_startup
    logger.info("Rate limiter backend: %s", _rate_limit_startup.init_rate_limiter())
    from app.core.session_store import get_session_store
    from app.routers import project as project_router
    app.state.project_store = get_session_store()
    project_router._store = app.state.project_store
    logger.info("Project session store ready: %s",
                type(app.state.project_store).__name__)
    loaded = load_agents()
    logger.info("Loaded %d runtime agents: %s", len(loaded), ", ".join(sorted(loaded.keys())))
    from app.core import hydration_scheduler
    hydration_scheduler.start()
    try:
        yield
    finally:
        await hydration_scheduler.stop()


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
    # Enumerate methods/headers rather than "*" — a credentialed CORS config
    # should not reflect arbitrary methods/headers back to the browser.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


# NOTE: a previous "file upload security" middleware was removed here. It
# called `await request.body()` on every /upload request, which buffered the
# entire multipart body (including the file) into memory — a memory-DoS that
# also defeated UploadFile's on-disk spooling. Its security check was dead
# code: it json.loads()'d a multipart body, always failed, and never ran the
# validator. Upload validation (size, extension, filename, path traversal)
# lives in app/routers/upload.py where the file is a proper UploadFile.


# ── Rate limiting ──────────────────────────────────────────────────────────
# Per-caller rate limiting on every request — including JWT sessions, which
# the per-API-key limiter never covered. Controlled by RATE_LIMIT_PER_MINUTE.
from app.core import rate_limit as _rate_limit
from app.core import jwt_auth as _jwt_auth

_RATE_LIMIT_EXEMPT_PREFIXES = ("/static", "/dashboard", "/assets")
_RATE_LIMIT_EXEMPT_EXACT = {
    # PR #98: /v1/metrics removed — it returns per-block execution counts +
    # latencies + error counts, which is operational data we should not
    # leak to anonymous callers. /metrics (Prometheus exposition) stays in
    # the exempt list because Prometheus scrapers typically don't auth and
    # the counter set there is intentionally limited to non-sensitive
    # request/response totals.
    "/", "/health", "/v1/health", "/metrics", "/docs", "/redoc", "/openapi.json",
}


def _rate_limit_identity(request: Request) -> str:
    """Identify the caller for rate-limiting: user id (JWT), hashed API key,
    or client IP for an unauthenticated request."""
    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer "):
        token = authz[7:].strip()
        try:
            payload = _jwt_auth.decode_token(token)
            if payload.get("user_id"):
                return f"user:{payload['user_id']}"
        except Exception:
            pass
        import hashlib
        return "key:" + hashlib.sha256(token.encode()).hexdigest()[:24]
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


@app.middleware("http")
async def observability_middleware_wrapper(request: Request, call_next):
    return await observability_middleware(request, call_next)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path in _RATE_LIMIT_EXEMPT_EXACT
        or any(path.startswith(p) for p in _RATE_LIMIT_EXEMPT_PREFIXES)
    ):
        return await call_next(request)
    if not _rate_limit.check_and_record(_rate_limit_identity(request)):
        return JSONResponse(
            status_code=429,
            content={"status": "error",
                     "error": "Rate limit exceeded — too many requests."},
        )
    response = await call_next(request)
    if (
        sentry_enabled()
        and response.status_code >= 500
        and not getattr(request.state, "sentry_captured", False)
    ):
        import sentry_sdk
        sentry_sdk.capture_message(
            f"5xx response: {request.method} {request.url.path} ({response.status_code})",
            level="error",
        )
    return response


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
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    if sentry_enabled() and exc.status_code >= 500:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        request.state.sentry_captured = True
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
    if sentry_enabled():
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        request.state.sentry_captured = True
    # Never leak internal stack traces in the response — log them, return generic.
    return JSONResponse(
        status_code=500,
        content=_envelope(500, "Internal server error", "INTERNAL_ERROR"),
    )


# Include all routers
app.include_router(blocks.router)
app.include_router(execute.router)
app.include_router(feedback_router.router)
app.include_router(chain.router)
app.include_router(chat.router)
app.include_router(upload.router)
app.include_router(auth.router)
app.include_router(memory.router)
app.include_router(monitoring.router)
app.include_router(projects.router)
from app.routers import exports as exports_router  # noqa: E402 — late import to keep diff small
app.include_router(exports_router.router)
from app.routers import usage as usage_router  # noqa: E402
app.include_router(usage_router.router)
from app.routers import admin_photos as admin_photos_router  # noqa: E402
app.include_router(admin_photos_router.router)
from app.routers import chat_photos as chat_photos_router  # noqa: E402
app.include_router(chat_photos_router.router)
app.include_router(doc_search.router)
app.include_router(rag_router.router)
app.include_router(redline.router)
app.include_router(project.router)
app.include_router(users.router)
app.include_router(doc_types.router)
app.include_router(workflows.router)
app.include_router(health.router)
app.include_router(mcp.router)
# Mount the MCP SSE POST endpoint directly on the app — include_router does
# not propagate Starlette Mount routes (no-op if MCP SSE deps are absent).
mcp.mount_message_endpoint(app)
app.include_router(drive.router)
app.include_router(agents_router.router)
app.include_router(hydration_router.router)
# Debug routes — only in non-production environments
env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).strip().lower()
if env in {"dev", "development", "local", "test", "testing"}:
    app.include_router(debug.router)

# Admin diagnostic routes — mounted in ALL environments. Endpoints are
# role-gated (admin only); without them, a shell-less Render deploy has
# no way to inspect the production index.
from app.routers import admin as admin_router  # noqa: E402
app.include_router(admin_router.router)

# Mount static files. The frontend bundle (frontend/dist) is a build artifact
# that is absent in CI and fresh checkouts; StaticFiles raises RuntimeError at
# import time if its directory is missing, so mount each frontend path only
# when it exists. app/static is committed, so it stays unconditional.
app.mount("/static", StaticFiles(directory="app/static"), name="static")
if os.path.isdir("frontend/dist"):
    app.mount("/dashboard", StaticFiles(directory="frontend/dist", html=True), name="dashboard")
if os.path.isdir("frontend/dist/assets"):
    app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")

# static.router includes a catch-all SPA fallback. Register it LAST so
# specific API routes and StaticFiles mounts are matched first; only
# unmatched GETs fall through to serve frontend/dist/index.html.
app.include_router(static.router)
