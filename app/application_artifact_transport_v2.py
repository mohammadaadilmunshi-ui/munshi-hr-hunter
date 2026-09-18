"""Authenticated Apply->Hunter execution bridge.

Read-only plan/artifact transport remains unchanged.  The same authenticated
private bridge also exposes narrowly scoped submit-authorization issue/claim
and verified production-receipt ingestion controls.
"""
from __future__ import annotations
import hashlib, hmac, json, os, time
from collections.abc import Callable, Mapping
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app import (
    application_plan_v2,
    cover_letter_artifact_v1,
    native_resume_artifact_v5,
    application_submit_authorization_v1,
    application_production_submission_receipt_v1,
)
from app.database import get_setting
from app.secure_vault import read_secret

REQUEST_VERSION = "munshi-application-execution-request-v1"
RESPONSE_VERSION = "munshi-application-execution-response-v1"
PURPOSE_PLAN_CURRENT = "PLAN_CURRENT"
PURPOSE_ARTIFACT_BYTES = "ARTIFACT_BYTES"
PURPOSE_COVER_LETTER_BYTES = "COVER_LETTER_BYTES"
PURPOSE_AUTOAPPLY_CONFIG = "AUTOAPPLY_CONFIG"
PURPOSE_AUTOAPPLY_CREDENTIAL = "AUTOAPPLY_CREDENTIAL"
PURPOSE_SUBMIT_AUTH_ISSUE = "SUBMIT_AUTHORIZATION_ISSUE"
PURPOSE_SUBMIT_AUTH_CLAIM = "SUBMIT_AUTHORIZATION_CLAIM"
PURPOSE_SUBMIT_AUTH_READ = "SUBMIT_AUTHORIZATION_READ"
PURPOSE_PRODUCTION_RECEIPT_INGEST = "PRODUCTION_RECEIPT_INGEST"
CONTROL_PURPOSES = frozenset({PURPOSE_SUBMIT_AUTH_ISSUE,PURPOSE_SUBMIT_AUTH_CLAIM,PURPOSE_SUBMIT_AUTH_READ,PURPOSE_PRODUCTION_RECEIPT_INGEST})
MAX_SKEW_SECONDS = 120
REQUIRED = {"version","request_id","purpose","tenant_id","user_id","application_id","plan_id","plan_digest","artifact_id","artifact_reference","artifact_sha256"}
CONTROL_REQUIRED = {"version","request_id","purpose","tenant_id","user_id","application_id","plan_id","plan_digest","payload"}

def _canonical(v: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(v),sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()

def _secret() -> bytes:
    v=str(os.getenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET") or "")
    if len(v)<16: raise RuntimeError("Apply handoff HMAC secret is not configured.")
    return v.encode()

def _header(h: Mapping[str,str], n:str)->str:
    for k,v in h.items():
        if str(k).casefold()==n.casefold(): return str(v)
    return ""

def _verify_signature(body:bytes,headers:Mapping[str,str],*,now:int|None=None)->str:
    d=hashlib.sha256(body).hexdigest(); eid=_header(headers,"X-Munshi-Event-Id"); tsx=_header(headers,"X-Munshi-Timestamp")
    if _header(headers,"X-Munshi-Content-SHA256")!=d: raise PermissionError("Execution bridge content digest is invalid.")
    try: ts=int(tsx)
    except ValueError as e: raise PermissionError("Execution bridge timestamp is invalid.") from e
    if abs((int(time.time()) if now is None else int(now))-ts)>MAX_SKEW_SECONDS: raise PermissionError("Execution bridge request is stale.")
    exp=hmac.new(_secret(),f"{eid}.{ts}.{d}".encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(_header(headers,"X-Munshi-Signature"),f"sha256={exp}"): raise PermissionError("Execution bridge signature is invalid.")
    return eid

def _decode(body:bytes)->dict[str,Any]:
    try: p=json.loads(body)
    except json.JSONDecodeError as e: raise ValueError("Execution bridge body is invalid JSON.") from e
    if not isinstance(p,dict) or _canonical(p)!=body: raise ValueError("Execution bridge request must use canonical JSON.")
    return p

def _verify(body:bytes,headers:Mapping[str,str],*,purpose:str,now:int|None=None)->dict[str,Any]:
    if purpose not in {
        PURPOSE_PLAN_CURRENT,
        PURPOSE_ARTIFACT_BYTES,
        PURPOSE_COVER_LETTER_BYTES,
        PURPOSE_AUTOAPPLY_CONFIG,
        PURPOSE_AUTOAPPLY_CREDENTIAL,
    }: raise ValueError("Unsupported execution bridge purpose.")
    eid=_verify_signature(body,headers,now=now); p=_decode(body)
    if set(p)!=REQUIRED: raise ValueError("Execution bridge request shape is invalid.")
    if p.get("version")!=REQUEST_VERSION or p.get("purpose")!=purpose: raise ValueError("Execution bridge request version/purpose is invalid.")
    if p.get("request_id")!=eid or not eid: raise PermissionError("Execution bridge event identity mismatch.")
    return p

def _verify_control(body:bytes,headers:Mapping[str,str],*,now:int|None=None)->dict[str,Any]:
    eid=_verify_signature(body,headers,now=now); p=_decode(body)
    if set(p)!=CONTROL_REQUIRED: raise ValueError("Execution bridge control request shape is invalid.")
    if p.get("version")!=REQUEST_VERSION or p.get("purpose") not in CONTROL_PURPOSES: raise ValueError("Execution bridge control version/purpose is invalid.")
    if p.get("request_id")!=eid or not eid: raise PermissionError("Execution bridge event identity mismatch.")
    if not isinstance(p.get("payload"),dict): raise ValueError("Execution bridge control payload must be an object.")
    return p

def _binding(snapshot:Mapping[str,Any],purpose:str)->dict[str,Any]:
    if purpose==PURPOSE_COVER_LETTER_BYTES:
        v=snapshot.get("cover_letter")
        if not isinstance(v,Mapping): raise PermissionError("Application Plan has no cover-letter artifact binding.")
        return dict(v)
    return dict(snapshot["resume"])

def _authorized(p:Mapping[str,Any],*,purpose:str):
    plan=application_plan_v2.executable_plan(str(p["plan_id"])); snap=dict(plan["snapshot"]); a=_binding(snap,purpose); owner=dict(snap.get("owner_binding") or {})
    checks=(str(plan["tenant_id"])==str(p["tenant_id"]),str(plan["user_id"])==str(p["user_id"]),str(owner.get("tenant_id") or plan["tenant_id"])==str(p["tenant_id"]),str(owner.get("user_id") or plan["user_id"])==str(p["user_id"]),str(plan["application_id"])==str(p["application_id"]),str(plan["plan_digest"])==str(p["plan_digest"]),str(a["artifact_id"])==str(p["artifact_id"]),str(a["artifact_reference"])==str(p["artifact_reference"]),str(a["artifact_sha256"])==str(p["artifact_sha256"]),snap.get("submission_authority") is False)
    if not all(checks): raise PermissionError("Execution bridge request conflicts with immutable plan binding.")
    return plan,snap,a

def _headers(*,request_id:str,purpose:str,body:bytes,plan_digest:str)->dict[str,str]:
    d=hashlib.sha256(body).hexdigest(); sig=hmac.new(_secret(),f"{request_id}.{purpose}.{d}.{plan_digest}".encode(),hashlib.sha256).hexdigest()
    return {"X-Munshi-Response-Event-Id":request_id,"X-Munshi-Response-Purpose":purpose,"X-Munshi-Response-SHA256":d,"X-Munshi-Plan-Digest":plan_digest,"X-Munshi-Response-Signature":f"sha256={sig}"}

def _control_response(body:bytes,headers:Mapping[str,str],*,now:int|None=None):
    p=_verify_control(body,headers,now=now); purpose=str(p["purpose"]); payload=dict(p["payload"])
    for key in ("tenant_id","user_id","application_id","plan_id","plan_digest"):
        source=payload
        if purpose==PURPOSE_SUBMIT_AUTH_CLAIM: source=dict(payload.get("authorization") or {})
        elif purpose==PURPOSE_PRODUCTION_RECEIPT_INGEST: source=dict(payload.get("receipt") or {})
        if str(source.get(key) or "")!=str(p[key]): raise PermissionError(f"Execution bridge control binding mismatch: {key}.")
    if purpose==PURPOSE_SUBMIT_AUTH_ISSUE:
        result=application_submit_authorization_v1.issue_authorization(payload)
    elif purpose==PURPOSE_SUBMIT_AUTH_READ:
        result=application_submit_authorization_v1.current_authorization(payload)
    elif purpose==PURPOSE_SUBMIT_AUTH_CLAIM:
        result=application_submit_authorization_v1.claim_authorization(dict(payload.get("authorization") or {}),claimant_id=str(payload.get("claimant_id") or ""))
    else:
        result=application_production_submission_receipt_v1.ingest_receipt(dict(payload.get("receipt") or {}))
    out=_canonical({"version":RESPONSE_VERSION,"request_id":str(p["request_id"]),"purpose":purpose,"plan_id":str(p["plan_id"]),"plan_digest":str(p["plan_digest"]),"result":result})
    return out,_headers(request_id=str(p["request_id"]),purpose=purpose,body=out,plan_digest=str(p["plan_digest"]))

def _autoapply_preferences()->dict[str,Any]:
    raw=dict(get_setting("product_autoapply_preferences_v1", {}) or {})
    auth_mode=str(raw.get("auth_mode") or "subscription").strip().casefold()
    if auth_mode not in {"subscription","api"}:
        auth_mode="subscription"
    model=str(raw.get("model") or "sonnet").strip().casefold()
    if not model or len(model)>120:
        model="sonnet"
    try:
        max_turns=max(1,min(int(raw.get("max_turns",40)),200))
    except (TypeError,ValueError):
        max_turns=40
    try:
        max_cost=max(0.0,min(float(raw.get("max_cost_per_application_usd",1.0)),100.0))
    except (TypeError,ValueError):
        max_cost=1.0
    return {
        "enabled":bool(raw.get("enabled",False)),
        "authMode":auth_mode,
        "model":model,
        "headless":bool(raw.get("headless",True)),
        "maxTurns":max_turns,
        "maxCostPerApplicationUsd":round(max_cost,2),
        # Model recovery never carries final-submit authority. That remains
        # exclusively in Hunter's one-use submit authorization contract.
        "allowFinalSubmit":False,
        "challengeServiceEnabled":False,
    }

def autoapply_config_response(body:bytes,headers:Mapping[str,str],*,now:int|None=None):
    p=_verify(body,headers,purpose=PURPOSE_AUTOAPPLY_CONFIG,now=now)
    plan,_,_=_authorized(p,purpose=PURPOSE_AUTOAPPLY_CONFIG)
    config=_autoapply_preferences()
    out=_canonical({
        "version":RESPONSE_VERSION,
        "request_id":str(p["request_id"]),
        "purpose":PURPOSE_AUTOAPPLY_CONFIG,
        "plan_id":str(plan["plan_id"]),
        "plan_digest":str(plan["plan_digest"]),
        "config":config,
    })
    return out,_headers(
        request_id=str(p["request_id"]),
        purpose=PURPOSE_AUTOAPPLY_CONFIG,
        body=out,
        plan_digest=str(plan["plan_digest"]),
    )

def autoapply_credential_response(body:bytes,headers:Mapping[str,str],*,now:int|None=None):
    p=_verify(body,headers,purpose=PURPOSE_AUTOAPPLY_CREDENTIAL,now=now)
    plan,_,_=_authorized(p,purpose=PURPOSE_AUTOAPPLY_CREDENTIAL)
    config=_autoapply_preferences()
    if config["enabled"] is not True or config["authMode"]!="api":
        raise PermissionError("Dashboard AutoApply API authentication is not enabled.")
    value=read_secret("autoapply_anthropic_api_key")
    if not value:
        raise LookupError("Dashboard Anthropic API credential is unavailable.")
    plaintext=value.encode("utf-8")
    if len(plaintext)>16384:
        raise RuntimeError("Dashboard Anthropic API credential is invalid.")

    request_id=str(p["request_id"])
    plan_digest=str(plan["plan_digest"])
    key=hmac.new(
        _secret(),
        f"autoapply-credential:{request_id}:{plan_digest}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    nonce=os.urandom(12)
    aad=f"{request_id}.{plan_digest}.autoapply_anthropic_api_key".encode("utf-8")
    data=nonce+AESGCM(key).encrypt(nonce,plaintext,aad)

    response_headers=_headers(
        request_id=request_id,
        purpose=PURPOSE_AUTOAPPLY_CREDENTIAL,
        body=data,
        plan_digest=plan_digest,
    )
    response_headers.update({
        "X-Munshi-Credential-Type":"autoapply_anthropic_api_key",
        "X-Munshi-Credential-Encryption":"aes-gcm-v1",
        "X-Munshi-Submission-Authority":"false",
    })
    return data,response_headers

def current_plan_response(body:bytes,headers:Mapping[str,str],*,now:int|None=None):
    peek=_decode(body)
    if peek.get("purpose") in CONTROL_PURPOSES: return _control_response(body,headers,now=now)
    p=_verify(body,headers,purpose=PURPOSE_PLAN_CURRENT,now=now); plan,_,_=_authorized(p,purpose=PURPOSE_PLAN_CURRENT)
    out=_canonical({"version":RESPONSE_VERSION,"request_id":str(p["request_id"]),"purpose":PURPOSE_PLAN_CURRENT,"plan_id":str(plan["plan_id"]),"plan_digest":str(plan["plan_digest"]),"fresh":True,"submission_authority":False})
    return out,_headers(request_id=str(p["request_id"]),purpose=PURPOSE_PLAN_CURRENT,body=out,plan_digest=str(plan["plan_digest"]))

def _artifact_response(body:bytes,headers:Mapping[str,str],*,purpose:str,reader:Callable[[str],bytes],now:int|None=None):
    p=_verify(body,headers,purpose=purpose,now=now); plan,_,a=_authorized(p,purpose=purpose); data=reader(str(a["artifact_id"])); d=hashlib.sha256(data).hexdigest()
    if d!=str(a["artifact_sha256"]): raise RuntimeError("Immutable Hunter artifact bytes do not match Application Plan digest.")
    hs=_headers(request_id=str(p["request_id"]),purpose=purpose,body=data,plan_digest=str(plan["plan_digest"])); hs.update({"X-Munshi-Artifact-Id":str(a["artifact_id"]),"X-Munshi-Artifact-SHA256":d,"X-Munshi-Submission-Authority":"false"}); return data,hs

def artifact_response(body:bytes,headers:Mapping[str,str],*,now:int|None=None):
    return _artifact_response(body,headers,purpose=PURPOSE_ARTIFACT_BYTES,reader=native_resume_artifact_v5.artifact_bytes,now=now)

def cover_letter_response(body:bytes,headers:Mapping[str,str],*,now:int|None=None):
    return _artifact_response(body,headers,purpose=PURPOSE_COVER_LETTER_BYTES,reader=cover_letter_artifact_v1.artifact_bytes,now=now)
