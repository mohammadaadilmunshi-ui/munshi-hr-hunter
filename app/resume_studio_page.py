"""Compatibility entry point for the strengthened Native Resume Studio UI."""
from app.resume_studio_page_v3 import render as _render
from app import resume_studio_page_v3 as _v3
from app import resume_studio_page_v2 as _v2
from app import native_resume_service_v4 as _v4
from app.resume_studio_source_workspace_v32 import source_workspace as _source_workspace

# Preserve the existing V3.2 intake/profile UI while upgrading only the writer
# operations used by the V2 application workspace to the truth-bound Phase 4
# service. This avoids duplicating or destabilizing the proven Streamlit UI.
_v3._source_workspace = _source_workspace
_v2.ensure_schema = _v4.ensure_schema
_v2.generate_resume = _v4.generate_resume
_v2.get_version = _v4.get_version
_v2.list_versions = _v4.list_versions

render = _render

__all__ = ["render"]
