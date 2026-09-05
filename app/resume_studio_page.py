"""Compatibility entry point for the current Native Resume Studio UI."""
from app import resume_studio_page_v3 as _v3
from app.resume_studio_source_workspace_v32 import source_workspace as _source_workspace

# V3.2 hotfix: preserve the existing V3.1 render/extraction authority while
# replacing only the Master Resume source workspace with the safe two-pass
# widget-state transition.
_v3._source_workspace = _source_workspace
render = _v3.render

__all__ = ["render"]
