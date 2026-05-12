"""QCS Center — FastAPI application entry point.

Start with:
    qcs-center
    # or:
    python -m qcs_center.app

Environment variables (see config.py):
    QCS_CENTER_HOST      bind address   (default 0.0.0.0)
    QCS_CENTER_PORT      TCP port       (default 8080)
    QCS_CENTER_API_KEY   shared Bearer token — MUST be set
    QCS_CENTER_DB_PATH   SQLite db path (default ./center.db)
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from qcs_center import db, router_agents, router_jobs

# ── Auth ──────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


def _api_key() -> str:
    key = config.QCS_CENTER_API_KEY
    if not key:
        raise RuntimeError(
            "QCS_CENTER_API_KEY is not set — center cannot start securely. "
            "Set the environment variable before launching."
        )
    return key


async def require_auth(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    """FastAPI dependency: validates the Bearer token on every request."""
    if creds.credentials != _api_key():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield
    await db.close_db()


app = FastAPI(
    title="QCS Center",
    description=(
        "Central command plane for Oracle EBS test automation. "
        "Clients submit recording jobs; Windows agents poll for and execute them."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

_auth_dep = [Depends(require_auth)]

app.include_router(router_jobs.router,   prefix="/api/v1", dependencies=_auth_dep)
app.include_router(router_agents.router, prefix="/api/v1", dependencies=_auth_dep)


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    """Unauthenticated health check for load balancers / probes."""
    return {"status": "ok"}


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Console-script entry point: qcs-center."""
    try:
        _api_key()  # fail-fast if key not set
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "qcs_center.app:app",
        host=config.QCS_CENTER_HOST,
        port=config.QCS_CENTER_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
