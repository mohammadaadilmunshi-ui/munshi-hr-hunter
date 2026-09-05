"""FastAPI entrypoint with persistent dashboard trusted-device authentication."""
from __future__ import annotations

from app.api import app
from app.device_auth import router as device_auth_router


app.include_router(device_auth_router)
