"""Local value-free semantic embedding retrieval for MUNSHI Universal Brain.

Only employer/browser structure and ontology metadata are embedded. Candidate
values, protected answers, credentials, secrets, OTP/CAPTCHA material and
submission authority never enter this lane.

The production provider is the existing local Ollama service. Ontology vectors
are cached by ontology fingerprint + model. Query vectors are ephemeral.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app import candidate_ontology_v1 as ontology

EMBEDDING_VERSION = "universal-semantic-embedding-v1"
_ENABLED_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_ENABLED"
_MODEL_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_MODEL"
_TIMEOUT_ENV = "MUNSHI_UNIVERSAL_EMBEDDING_TIMEOUT_SECONDS"
_BASE_URL_ENV = "OLLAMA_BASE_URL"
_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_BASE_URL = "http://ollama:11434"

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[list[dict[str, Any]], list[list[float]]]] = {}


def enabled() -> bool:
    return str(os.getenv(_ENABLED_ENV) or "").strip().casefold() in {"1","true","yes","on"}


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _observation_text(observation: Mapping[str, Any]) -> str:
    parts = [
        _clean(observation.get("label")),
        _clean(observation.get("raw_question") or observation.get("rawText") or observation.get("question")),
        _clean(observation.get("section_heading") or observation.get("sectionHeading")),
        _clean(observation.get("nearby_text") or observation.get("nearbyText")),
        _clean(observation.get("placeholder")),
        _clean(observation.get("name") or observation.get("fieldName")),
        _clean(observation.get("autocomplete")),
        _clean(observation.get("input_type") or observation.get("inputType")),
        _clean(observation.get("role")),
    ]
    options = observation.get("options")
    if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        option_text = " | ".join(_clean(item, 200) for item in list(options)[:40] if _clean(item,200))
        if option_text:
            parts.append("options: " + option_text)
    return " ; ".join(part for part in parts if part)[:12000]


def _concept_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in ontology.all_concepts():
        # Repeated-entity dynamic concepts are represented by their canonical
        # base concept. Runtime repeat scoping remains a separate binding concern.
        text = " ; ".join(
            part for part in (
                item.description,
                item.concept_id.replace(".", " ").replace("_", " "),
                "aliases: " + " | ".join(item.aliases[:32]) if item.aliases else "",
                "browser semantics: " + " | ".join(item.browser_semantics[:16]) if item.browser_semantics else "",
                "category: " + item.category,
                "answer mode: " + item.answer_mode,
            ) if part
        )
        rows.append({
            "concept_id": item.concept_id,
            "text": text[:6000],
            "sensitivity": item.sensitivity,
            "answer_mode": item.answer_mode,
            "category": item.category,
            "description": item.description[:800],
        })
    return rows


def _ontology_fingerprint(rows: Sequence[Mapping[str, Any]]) -> str:
    payload=[(r["concept_id"],r["text"]) for r in rows]
    return hashlib.sha256(
        json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    ).hexdigest()


def _embed_batch(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    model=str(os.getenv(_MODEL_ENV) or _DEFAULT_MODEL).strip()
    base=str(os.getenv(_BASE_URL_ENV) or _DEFAULT_BASE_URL).strip().rstrip("/")
    try:
        timeout=float(os.getenv(_TIMEOUT_ENV) or "20")
    except ValueError:
        timeout=20.0
    if not model or not base:
        raise RuntimeError("Universal semantic embedding provider is not configured.")
    try:
        response=httpx.post(
            base + "/api/embed",
            json={"model": model, "input": list(texts), "truncate": True},
            timeout=max(2.0,min(timeout,60.0)),
        )
        response.raise_for_status()
        body=response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Local semantic embedding request failed.") from exc
    values=body.get("embeddings") if isinstance(body,Mapping) else None
    if not isinstance(values,list) or len(values)!=len(texts):
        raise RuntimeError("Local semantic embedding response shape is invalid.")
    result=[]
    for row in values:
        if not isinstance(row,list) or not row:
            raise RuntimeError("Local semantic embedding vector is invalid.")
        result.append([float(v) for v in row])
    return result


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left)!=len(right) or not left:
        return 0.0
    dot=sum(a*b for a,b in zip(left,right))
    ln=math.sqrt(sum(a*a for a in left))
    rn=math.sqrt(sum(b*b for b in right))
    if ln<=0.0 or rn<=0.0:
        return 0.0
    return max(-1.0,min(1.0,dot/(ln*rn)))


def _ontology_vectors() -> tuple[list[dict[str, Any]], list[list[float]]]:
    rows=_concept_rows()
    model=str(os.getenv(_MODEL_ENV) or _DEFAULT_MODEL).strip()
    key=(model,_ontology_fingerprint(rows))
    with _CACHE_LOCK:
        cached=_CACHE.get(key)
    if cached is not None:
        return cached
    vectors=_embed_batch([str(row["text"]) for row in rows])
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[key]=(rows,vectors)
    return rows,vectors


def retrieve(
    observation: Mapping[str, Any],
    *,
    top_k: int=8,
) -> list[dict[str, Any]]:
    """Return ontology candidates ranked by local semantic embedding similarity."""
    if not enabled():
        return []
    query=_observation_text(observation)
    if not query:
        return []
    rows,vectors=_ontology_vectors()
    [query_vector]=_embed_batch([query])
    ranked=[]
    for row,vector in zip(rows,vectors):
        cosine=_cosine(query_vector,vector)
        # Convert [-1,1] cosine to [0,1] for a stable score contract.
        score=(cosine+1.0)/2.0
        ranked.append({
            "concept_id":row["concept_id"],
            "score":round(score,6),
            "sensitivity":row["sensitivity"],
            "answer_mode":row["answer_mode"],
            "category":row["category"],
            "description":row["description"],
        })
    ranked.sort(key=lambda item:(-float(item["score"]),str(item["concept_id"])))
    return ranked[:max(1,min(int(top_k),16))]


def retrieve_fail_closed(
    observation: Mapping[str, Any],
    *,
    top_k: int=8,
) -> list[dict[str, Any]]:
    try:
        return retrieve(observation,top_k=top_k)
    except (RuntimeError,ValueError,TypeError,httpx.HTTPError):
        return []


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def status() -> dict[str, Any]:
    return {
        "version":EMBEDDING_VERSION,
        "enabled":enabled(),
        "provider":"ollama-local",
        "model":str(os.getenv(_MODEL_ENV) or _DEFAULT_MODEL).strip(),
        "candidate_values_exposed":False,
        "external_provider":False,
    }
