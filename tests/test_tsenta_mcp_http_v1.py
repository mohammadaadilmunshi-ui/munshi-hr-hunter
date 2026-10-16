from __future__ import annotations

import json

import httpx
import pytest

from app.tsenta_mcp_http_v1 import TsentaMcpReadClient
from app.tsenta_oauth_v1 import TsentaOAuthError


def test_bounded_sse_and_tools_are_read_only():
    calls=[]
    def handler(request):
        body=json.loads(request.content)
        calls.append(body["method"])
        if body["method"]=="notifications/initialized":
            return httpx.Response(202)
        if body["method"]=="initialize":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
                "result":{"protocolVersion":"2025-11-25"}},headers={"Mcp-Session-Id":"synthetic"})
        if body["method"]=="tools/list":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
                "result":{"tools":[{"name":"get-profile-status"},
                                   {"name":"apply-to-job"}]}})
        if body["method"]=="tools/call":
            assert body["params"]["name"]=="get-profile-status"
            event=json.dumps({"jsonrpc":"2.0","id":body["id"],
                "result":{"structuredContent":{"result":{"hasResume":True}}}})
            return httpx.Response(200,text="event: message\ndata: "+event+"\n\n",
                                  headers={"content-type":"text/event-stream"})
        raise AssertionError(body["method"])

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        TsentaMcpReadClient("synthetic-bearer",client) as mcp,
    ):
        mcp.initialize()
        data=mcp.call_read("get_profile_status")
        assert data["structuredContent"]["result"]["hasResume"] is True
        with pytest.raises(TsentaOAuthError):
            mcp.call_read("apply_to_job",{})
    assert calls==["initialize","notifications/initialized","tools/list","tools/call"]


def test_provider_tool_error_never_returns_as_valid_profile_data():
    def handler(request):
        body=json.loads(request.content)
        if body["method"]=="notifications/initialized":
            return httpx.Response(202)
        if body["method"]=="initialize":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
                "result":{"protocolVersion":"2025-11-25"}})
        if body["method"]=="tools/list":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
                "result":{"tools":[{"name":"get_profile_status"}]}})
        return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
            "error":{"code":-32000,"message":"private profile error"}})
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        TsentaMcpReadClient("synthetic-bearer",client) as mcp,
    ):
        mcp.initialize()
        with pytest.raises(TsentaOAuthError) as failure:
            mcp.call_read("get_profile_status")
        assert "private profile" not in str(failure.value)
