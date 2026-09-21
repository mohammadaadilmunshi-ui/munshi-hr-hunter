"""Local value-free semantic embedding retrieval for MUNSHI Universal Brain.

Only employer/browser structure and ontology metadata are embedded. Candidate
values, protected answers, credentials, secrets, OTP/CAPTCHA material and
submission authority never enter this lane.

The production provider is the existing local Ollama service. Ontology vectors
are cached by embedding model + ontology fingerprint in Hunter's shared runtime
so short-lived prepare workers do not rebuild the full ontology on every run.
Query vectors remain ephemeral.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from app import candidate_ontology_v1 as ontology
from app import platform_config

EMBEDDING_VERSION = "universal-semantic-embedding-v1"
_CACHE_SCHEMA = "universal-semantic-embedding-cache-v1"
_ENABLED_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_ENABLED"
_MODEL_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_MODEL"
_TIMEOUT_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_TIMEOUT_SECONDS"
_BUILD_TIMEOUT_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_BUILD_TIMEOUT_SECONDS"
_BATCH_SIZE_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_BATCH_SIZE"
_KEEP_ALIVE_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_KEEP_ALIVE"
_CACHE_DIR_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_CACHE_DIR"
_BASE_URL_ENV = "OLLAMA_BASE_URL"
_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_BASE_URL = "http://ollama:11434"
_DEFAULT_KEEP_ALIVE = "10m"

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[list[dict[str, Any]], list[list[float]]]] = {}


def enabled() -> bool:
    return str(os.getenv(_ENABLED_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _observation_text(observation: Mapping[str, Any]) -> str:
    parts = [
        _clean(observation.get("label")),
        _clean(
            observation.get("raw_question")
            or observation.get("rawText")
            or observation.get("question")
        ),
        _clean(
            observation.get("section_heading") or observation.get("sectionHeading")
        ),
        _clean(observation.get("nearby_text") or observation.get("nearbyText")),
        _clean(observation.get("placeholder")),
        _clean(observation.get("name") or observation.get("fieldName")),
        _clean(observation.get("autocomplete")),
        _clean(observation.get("input_type") or observation.get("inputType")),
        _clean(observation.get("role")),
    ]
    options = observation.get("options")
    if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        option_text = " | ".join(
            _clean(item, 200)
            for item in list(options)[:40]
            if _clean(item, 200)
        )
        if option_text:
            parts.append("options: " + option_text)
    return " ; ".join(part for part in parts if part)[:12000]


def _concept_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in ontology.all_concepts():
        text = " ; ".join(
            part
            for part in (
                item.description,
                item.concept_id.replace(".", " ").replace("_", " "),
                "aliases: " + " | ".join(item.aliases[:32]) if item.aliases else "",
                "browser semantics: " + " | ".join(item.browser_semantics[:16])
                if item.browser_semantics
                else "",
                "category: " + item.category,
                "answer mode: " + item.answer_mode,
            )
            if part
        )
        rows.append(
            {
                "concept_id": item.concept_id,
                "text": text[:6000],
                "sensitivity": item.sensitivity,
                "answer_mode": item.answer_mode,
                "category": item.category,
                "description": item.description[:800],
            }
        )
    return rows


def _ontology_fingerprint(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = [(r["concept_id"], r["text"]) for r in rows]
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _model() -> str:
    return str(os.getenv(_MODEL_ENV) or _DEFAULT_MODEL).strip()


def _cache_root() -> Path:
    configured = str(os.getenv(_CACHE_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return platform_config.runtime_directory() / "universal_brain" / "embedding_cache"


def _cache_path(model: str, fingerprint: str) -> Path:
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("_") or "model"
    return _cache_root() / (
        f"{_CACHE_SCHEMA}-{safe_model}-{fingerprint}.json"
    )


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name) or str(default))
    except ValueError:
        value = default
    return value


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or str(default))
    except ValueError:
        value = default
    return value


def _embed_batch(
    texts: Sequence[str],
    *,
    timeout_seconds: float | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    model = _model()
    base = str(os.getenv(_BASE_URL_ENV) or _DEFAULT_BASE_URL).strip().rstrip("/")
    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else _float_env(_TIMEOUT_ENV, 20.0)
    )
    keep_alive = str(
        os.getenv(_KEEP_ALIVE_ENV) or _DEFAULT_KEEP_ALIVE
    ).strip() or _DEFAULT_KEEP_ALIVE
    if not model or not base:
        raise RuntimeError(
            "Universal semantic embedding provider is not configured."
        )
    try:
        response = httpx.post(
            base + "/api/embed",
            json={
                "model": model,
                "input": list(texts),
                "truncate": True,
                "keep_alive": keep_alive,
            },
            timeout=max(2.0, min(timeout, 90.0)),
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Local semantic embedding request failed.") from exc
    values = body.get("embeddings") if isinstance(body, Mapping) else None
    if not isinstance(values, list) or len(values) != len(texts):
        raise RuntimeError("Local semantic embedding response shape is invalid.")
    result: list[list[float]] = []
    for row in values:
        if not isinstance(row, list) or not row:
            raise RuntimeError("Local semantic embedding vector is invalid.")
        result.append([float(v) for v in row])
    return result


def _embed_ontology_rows(rows: Sequence[Mapping[str, Any]]) -> list[list[float]]:
    batch_size = max(1, min(_int_env(_BATCH_SIZE_ENV, 12), 32))
    timeout = max(
        5.0,
        min(_float_env(_BUILD_TIMEOUT_ENV, 45.0), 90.0),
    )
    texts = [str(row["text"]) for row in rows]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(
            _embed_batch(
                texts[start : start + batch_size],
                timeout_seconds=timeout,
            )
        )
    return vectors


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    ln = math.sqrt(sum(a * a for a in left))
    rn = math.sqrt(sum(b * b for b in right))
    if ln <= 0.0 or rn <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (ln * rn)))


def _valid_vectors(
    rows: Sequence[Mapping[str, Any]],
    vectors: Any,
) -> list[list[float]] | None:
    if not isinstance(vectors, list) or len(vectors) != len(rows) or not rows:
        return None
    dimension: int | None = None
    normalized: list[list[float]] = []
    for raw in vectors:
        if not isinstance(raw, list) or not raw:
            return None
        try:
            vector = [float(value) for value in raw]
        except (TypeError, ValueError):
            return None
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            return None
        normalized.append(vector)
    return normalized


def _load_persistent_cache(
    *,
    rows: list[dict[str, Any]],
    model: str,
    fingerprint: str,
) -> list[list[float]] | None:
    path = _cache_path(model, fingerprint)
    try:
        if not path.is_file() or path.stat().st_size > 20_000_000:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if (
        payload.get("schema") != _CACHE_SCHEMA
        or payload.get("embedding_version") != EMBEDDING_VERSION
        or payload.get("model") != model
        or payload.get("ontology_fingerprint") != fingerprint
    ):
        return None
    expected_ids = [str(row["concept_id"]) for row in rows]
    if payload.get("concept_ids") != expected_ids:
        return None
    return _valid_vectors(rows, payload.get("vectors"))


def _store_persistent_cache(
    *,
    rows: list[dict[str, Any]],
    vectors: list[list[float]],
    model: str,
    fingerprint: str,
) -> None:
    validated = _valid_vectors(rows, vectors)
    if validated is None:
        raise RuntimeError("Refusing to persist invalid ontology embeddings.")
    path = _cache_path(model, fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _CACHE_SCHEMA,
        "embedding_version": EMBEDDING_VERSION,
        "model": model,
        "ontology_fingerprint": fingerprint,
        "concept_ids": [str(row["concept_id"]) for row in rows],
        "vector_dimension": len(validated[0]),
        "vectors": validated,
        "candidate_values_stored": False,
    }
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _ontology_vectors() -> tuple[list[dict[str, Any]], list[list[float]]]:
    rows = _concept_rows()
    model = _model()
    fingerprint = _ontology_fingerprint(rows)
    key = (model, fingerprint)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached

    vectors = _load_persistent_cache(
        rows=rows,
        model=model,
        fingerprint=fingerprint,
    )
    if vectors is None:
        vectors = _embed_ontology_rows(rows)
        _store_persistent_cache(
            rows=rows,
            vectors=vectors,
            model=model,
            fingerprint=fingerprint,
        )

    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[key] = (rows, vectors)
    return rows, vectors


def warm_cache() -> dict[str, Any]:
    """Build/load the ontology cache without embedding any candidate field."""
    rows, vectors = _ontology_vectors()
    model = _model()
    fingerprint = _ontology_fingerprint(rows)
    return {
        "version": EMBEDDING_VERSION,
        "model": model,
        "ontology_fingerprint": fingerprint,
        "concept_count": len(rows),
        "vector_dimension": len(vectors[0]) if vectors else 0,
        "cache_path": str(_cache_path(model, fingerprint)),
        "candidate_values_stored": False,
        "ready": bool(rows and vectors),
    }


def retrieve(
    observation: Mapping[str, Any],
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Return ontology candidates ranked by local semantic embedding similarity."""
    if not enabled():
        return []
    query = _observation_text(observation)
    if not query:
        return []
    rows, vectors = _ontology_vectors()
    [query_vector] = _embed_batch([query])
    ranked: list[dict[str, Any]] = []
    for row, vector in zip(rows, vectors):
        cosine = _cosine(query_vector, vector)
        score = (cosine + 1.0) / 2.0
        ranked.append(
            {
                "concept_id": row["concept_id"],
                "score": round(score, 6),
                "sensitivity": row["sensitivity"],
                "answer_mode": row["answer_mode"],
                "category": row["category"],
                "description": row["description"],
            }
        )
    ranked.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item["concept_id"]),
        )
    )
    return ranked[: max(1, min(int(top_k), 16))]


def retrieve_fail_closed(
    observation: Mapping[str, Any],
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    try:
        return retrieve(observation, top_k=top_k)
    except (RuntimeError, ValueError, TypeError, httpx.HTTPError):
        return []


def reset_cache(*, persistent: bool = False) -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    if not persistent:
        return
    try:
        root = _cache_root()
        if not root.is_dir():
            return
        for path in root.glob(f"{_CACHE_SCHEMA}-*.json"):
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def status() -> dict[str, Any]:
    rows = _concept_rows()
    model = _model()
    fingerprint = _ontology_fingerprint(rows)
    path = _cache_path(model, fingerprint)
    return {
        "version": EMBEDDING_VERSION,
        "enabled": enabled(),
        "provider": "ollama-local",
        "model": model,
        "candidate_values_exposed": False,
        "external_provider": False,
        "persistent_cache_ready": path.is_file(),
        "ontology_fingerprint": fingerprint,
        "cache_schema": _CACHE_SCHEMA,
    }
