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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

from app.blocks import BLOCK_REGISTRY
from app.dependencies import block_instances, _create_block_instance, init_blocks
from app.routers import (
    auth,
    blocks,
    chain,
    chat,
    debug,
    execute,
    health,
    memory,
    monitoring,
    static,
    upload,
)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all blocks eagerly at startup to avoid race conditions."""
    await init_blocks()
    yield


app = FastAPI(
    title="Cerebrum Blocks",
    description="Build AI Like Lego - Simple Block Execution API",
    version="2.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cerebrum-platform-frontend-fork.onrender.com",
        "https://cerebrum-platform-api-fork.onrender.com",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
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
app.include_router(static.router)
# Debug routes — only in non-production environments
env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).strip().lower()
if env in {"dev", "development", "local", "test", "testing"}:
    app.include_router(debug.router)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
