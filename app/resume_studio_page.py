"""Compatibility entry point for the strengthened Native Resume Studio UI."""
from app.resume_studio_page_v3 import render as _render
from app import resume_studio_page_v3 as _v3
from app import resume_studio_page_v2 as _v2
from app import native_resume_service_v4 as _v4
from app.profile_extraction_bridge_v1 import extract_profile_from_source as _local_profile_extract
from app.resume_studio_source_workspace_v32 import source_workspace as _source_workspace

# Preserve the existing V3.2 intake/profile UI while upgrading only the writer
# operations used by the V2 application workspace to the truth-bound Phase 4
# service. Profile extraction is intentionally local/evidence-only by default so
# building the Candidate Truth Profile never depends on an OpenAI credential.
_v3._source_workspace = _source_workspace
_v3.extract_profile_from_source = _local_profile_extract
_v2.ensure_schema = _v4.ensure_schema
_v2.generate_resume = _v4.generate_resume
_v2.get_version = _v4.get_version
_v2.list_versions = _v4.list_versions

render = _render

__all__ = ["render"]
