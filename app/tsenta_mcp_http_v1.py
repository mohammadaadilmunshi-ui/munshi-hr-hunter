"""Bounded authenticated MCP Streamable HTTP client for Tsenta read-only calls.

Legacy initialize/session protocol compatible; supports JSON and finite SSE.
No cookie copying, logging of token/response bodies, or application execution.
"""
from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from typing import Any

import httpx

from app.tsenta_oauth_v1 import OAUTH_BASE, TsentaOAuthError

MCP_URL = OAUTH_BASE + "/api/v1/mcp"
MAX_RESPONSE_BYTES = 2_000_000
READ_TOOLS = frozenset({
    "get_profile_status", "list_resume_profiles", "get_resume_profile",
    "get_apply_preferences", "get_auto_apply_state", "list_applications",
    "get_application_status", "get_application_review",
})
PROTO = "2025-11-25"


class TsentaMcpReadClient:
    def __init__(self, access_token: str, client: httpx.Client | None = None):
        if not isinstance(access_token,str) or not access_token.strip():
            raise TsentaOAuthError("An authenticated Tsenta server token is required.")
        self._token=access_token
        self._client=client or httpx.Client(timeout=15)
        self._owned=client is None
        self._session: str | None=None
        self._tools: dict[str,str]={}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if self._owned:
            self._client.close()

    def _send(self, method: str, params: dict[str,Any] | None=None, notification: bool=False) -> Any:
        request_id=secrets.randbelow(2**31) if not notification else None
        message:dict[str,Any]={"jsonrpc":"2.0","method":method}
        if params is not None:
            message["params"]=params
        if not notification:
            message["id"]=request_id
        headers={
            "Authorization":"Bearer "+self._token,
            "Accept":"application/json, text/event-stream",
            "Content-Type":"application/json",
            "MCP-Protocol-Version": PROTO,
        }
        if self._session:
            headers["MCP-Session-Id"]=self._session
        try:
            with self._client.stream("POST", MCP_URL, headers=headers, json=message,
                                     timeout=15, follow_redirects=False) as response:
                if response.status_code==401:
                    raise TsentaOAuthError("Tsenta backend authorization is expired.")
                if response.status_code not in (200,202):
                    raise TsentaOAuthError("Tsenta MCP server rejected the request.")
                if notification:
                    if response.status_code != 202:
                        raise TsentaOAuthError("Tsenta MCP did not acknowledge initialization.")
                    return None
                session=response.headers.get("mcp-session-id")
                if session and len(session) <= 256 and all(33 <= ord(x) <= 126 for x in session):
                    self._session=session
                content_type=response.headers.get("content-type","").split(";")[0].strip().lower()
                if content_type=="application/json":
                    body=bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body)>MAX_RESPONSE_BYTES:
                            raise TsentaOAuthError("Tsenta MCP response was too large.")
                    result=json.loads(body)
                    return self._extract(result, request_id)
                if content_type=="text/event-stream":
                    size=0
                    for line in response.iter_lines():
                        size+=len(line)
                        if size>MAX_RESPONSE_BYTES:
                            raise TsentaOAuthError("Tsenta MCP stream exceeded safe limits.")
                        if not line.startswith("data:"):
                            continue
                        try:
                            result=json.loads(line[5:].strip())
                        except (ValueError,TypeError):
                            continue
                        if isinstance(result,Mapping) and result.get("id")==request_id:
                            return self._extract(result,request_id)
                    raise TsentaOAuthError("Tsenta MCP stream contained no matching response.")
                raise TsentaOAuthError("Tsenta MCP response media type is unsupported.")
        except (httpx.HTTPError,ValueError) as error:
            raise TsentaOAuthError("Tsenta MCP transport was unavailable.") from error

    @staticmethod
    def _extract(response: Any, request_id: int) -> Any:
        if not isinstance(response,Mapping) or response.get("id")!=request_id:
            raise TsentaOAuthError("Tsenta MCP response identity did not match.")
        if "error" in response:
            raise TsentaOAuthError("Tsenta MCP reported a tool error.")
        if "result" not in response:
            raise TsentaOAuthError("Tsenta MCP response is missing its result.")
        return response["result"]

    def initialize(self) -> None:
        info=self._send("initialize", {
            "protocolVersion":PROTO,
            "clientInfo":{"name":"munshi-hunter-tsenta-readonly","version":"1.0.0"},
            "capabilities":{},
        })
        if not isinstance(info,Mapping) or not info.get("protocolVersion"):
            raise TsentaOAuthError("Tsenta MCP initialization was not confirmed.")
        self._send("notifications/initialized",notification=True)
        tools=self._send("tools/list",{})
        if not isinstance(tools,Mapping) or not isinstance(tools.get("tools"),list):
            raise TsentaOAuthError("Tsenta MCP tool inventory was unavailable.")
        for row in tools["tools"]:
            if not isinstance(row,Mapping):
                continue
            name=str(row.get("name") or "")
            normalized=name.replace("-","_")
            if normalized in READ_TOOLS and name:
                self._tools[normalized]=name

    def call_read(self,tool_name:str,arguments:Mapping[str,Any] | None=None) -> Any:
        if tool_name not in READ_TOOLS or tool_name not in self._tools:
            raise TsentaOAuthError("Tsenta read tool is not supported by this account.")
        result=self._send("tools/call",{
            "name":self._tools[tool_name],"arguments":dict(arguments or {}),
        })
        if not isinstance(result,Mapping):
            raise TsentaOAuthError("Tsenta MCP tool result was invalid.")
        if result.get("isError") is True or result.get("is_error") is True:
            raise TsentaOAuthError("Tsenta MCP tool reported an error.")
        return result
