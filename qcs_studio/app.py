"""QCS Studio - container/element repository UI backend."""
from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

import config
from qcs_studio import router

_bearer = HTTPBearer(auto_error=False)


def _api_key() -> str:
    return config.QCS_STUDIO_API_KEY.strip()


async def require_auth(creds: HTTPAuthorizationCredentials = Security(_bearer)) -> None:
    """Validate bearer auth when QCS_STUDIO_API_KEY is configured."""
    key = _api_key()
    if not key:
        return
    if creds is None or creds.credentials != key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


app = FastAPI(
    title="QCS Studio",
    description="Container and element curation studio for Oracle automation repository.",
    version="0.1.0",
)

app.include_router(router.router, prefix="/api/v1", dependencies=[Depends(require_auth)])


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


@app.get("/screenshots/{file_name}", include_in_schema=False)
def screenshot(file_name: str):
    path = config.REPO_DIR / "screenshots" / file_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path)


_STATIC_DIR = Path(__file__).parent / "web" / "dist"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="studio-web")
else:
    @app.get("/", include_in_schema=False)
    def root() -> HTMLResponse:
        return HTMLResponse(
            "<h2>QCS Studio backend is running.</h2>"
            "<p>Build frontend first: <code>cd qcs_studio/web; npm install; npm run build</code></p>"
        )


def main() -> None:
    """Console-script entry point: qcs-studio."""
    if not (Path(__file__).parent / "web").exists():
        print("ERROR: qcs_studio/web folder is missing.", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "qcs_studio.app:app",
        host=config.QCS_STUDIO_HOST,
        port=config.QCS_STUDIO_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
