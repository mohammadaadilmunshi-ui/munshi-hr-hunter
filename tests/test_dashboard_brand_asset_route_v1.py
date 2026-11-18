"""Production /app/static icon URLs must be served by FastAPI, not Streamlit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.device_auth import _PUBLIC_BRAND_ASSETS, router

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, base_url="https://dashboard.munshi.systems")


@pytest.mark.parametrize("asset", sorted(_PUBLIC_BRAND_ASSETS))
def test_exact_public_brand_url_returns_real_bytes_without_login(client: TestClient, asset: str):
    filename = asset.rsplit("/", 1)[-1]
    actual = (ROOT / "static" / filename).read_bytes()
    response = client.get(asset)
    assert response.status_code == 200, (asset, response.text[:120])
    assert response.content == actual
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    if filename == "manifest.json":
        assert response.headers["content-type"].startswith("application/manifest+json")
        manifest = json.loads(response.content)
        assert manifest["short_name"] == "MUNSHI"
    else:
        assert response.headers["content-type"].startswith("image/")


@pytest.mark.parametrize("asset", sorted(_PUBLIC_BRAND_ASSETS))
def test_exact_public_brand_url_supports_head(client: TestClient, asset: str):
    response = client.head(asset)
    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) == (ROOT / "static" / asset.rsplit("/", 1)[-1]).stat().st_size


@pytest.mark.parametrize(
    "unsafe",
    [
        "/app/static/unknown.png",
        "/app/static/icon-512.png.extra",
        "/app/static/app.py",
        "/app/static/secret.env",
        "/app/static/manifest.json%2Fanything",
        "/app/static/../dashboard.py",
        "/app/static/.env",
    ],
)
def test_never_serve_nonallowlisted_or_traversal_paths(client: TestClient, unsafe: str):
    response = client.get(unsafe, follow_redirects=False)
    assert response.status_code in {404, 400}, (unsafe, response.status_code)
    assert "image/" not in response.headers.get("content-type", "")


def test_route_does_not_expose_other_dashboard_api_endpoints(client: TestClient):
    assert client.get("/api/jobs").status_code == 404
    assert client.get("/_munshi-auth/login").status_code == 401
    assert client.get("/").status_code == 404


def test_edge_auth_does_not_allow_slash_variant_or_path_prefix(client: TestClient):
    # Starlette may normalize a trailing slash with a 307, but the production
    # Caddy forward-auth sees the unnormalized ORIGINAL URI and denies it.
    for path in (
        "/app/static/icon-512.png/",
        "/app/static/",
        "/app/static/icon-512.png/other",
        "/app/static/manifest.json%2Fother",
    ):
        result = client.get(
            "/_munshi-auth/verify",
            headers={"X-Munshi-Original-URI": path},
            follow_redirects=False,
        )
        assert result.status_code == 303, (path, result.status_code)
        assert result.headers["location"].startswith("/_munshi-auth/login?next=")
