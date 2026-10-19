"""Export a synthetic component preview, without starting an application server.

Run from the repository root: python scripts/render_studio_preview.py /tmp/munshi-studio.html
Uses the real Streamlit render tree with every data and action boundary mocked.
HTML adapters approximate native Streamlit controls; this is not staging proof.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest


PREVIEW_SOURCE = '''
import streamlit as st
from contextlib import ExitStack
from unittest.mock import patch
from types import SimpleNamespace
from app import product_pages as p
from app.product_ui import inject_css
from app.product_v22 import inject_v22_css, brand_logo_data_uri
from app.product_experience_vnext import inject_presentation_css, render_mobile_dock
from app.product_studio import inject_studio_css, workspace_location
from app.product_shell import NAVIGATION, _nav_link
st.set_page_config(layout="wide")
inject_css(); inject_v22_css(); inject_presentation_css(); inject_studio_css()
links = "".join(_nav_link(r,l,"dashboard") for r,l in NAVIGATION)
mobile = "".join(_nav_link(r,l,"dashboard",mobile=True) for r,l in (*NAVIGATION,("settings","Settings")))
with st.container(key="product_top_bar"):
 st.markdown(f'<header class="product-header"><a class="brand" href="?view=dashboard"><span class="brand-mark"><img src="{brand_logo_data_uri()}" width="38" height="38" alt=""></span>MUNSHI</a><nav class="product-nav" aria-label="Primary navigation">{links}</nav><div class="product-header-actions"><a class="product-nav-link" href="?view=settings">Settings</a><span class="status-pill-product">Discovery: Paused</span></div><details class="mobile-nav"><summary aria-label="Open navigation">Menu</summary><nav aria-label="Mobile navigation">{mobile}</nav></details></header>',unsafe_allow_html=True)
render_mobile_dock("dashboard")
st.markdown(workspace_location("Dashboard"),unsafe_allow_html=True)
rows=[
 {"id":81001,"title":"People Analytics Analyst","company_name":"Example Research Institute","location_raw":"New York, NY","hunter_score":96,"remote_type":"Hybrid","employment_type":"Full time"},
 {"id":81002,"title":"Human Resources Operations Coordinator","company_name":"Example International Education and Research Foundation","location_raw":"Philadelphia, PA","hunter_score":93,"remote_type":"On site","employment_type":"Full time"},
 {"id":81003,"title":"Talent Acquisition Analyst","company_name":"Example Health Systems","location_raw":"Remote, US","hunter_score":91,"remote_type":"Remote","employment_type":"Full time"},
 {"id":81004,"title":"Compensation & Benefits Analyst","company_name":"Example Financial Group","location_raw":"Newark, NJ","hunter_score":88,"remote_type":"Hybrid","employment_type":"Full time"},
]
counts={"sent_24h":0,"employer_confirmed":0,"needs_approval":2,"awaiting_receipt":0,"outcome_unknown":0,"tracked":4,"actively_processing":0,"recovery_needed":1}
with ExitStack() as mocks:
 for name,value in {
  "volume_policy": {"mode":"paused"}, "fetch_jobs":(rows,24),
  "tracker_rows":[], "_application_flow":{"cta":"prepare"},
  "_job_destination":SimpleNamespace(apply_via="Employer website",source="Synthetic fixture",original_url=""),
  "job_filters":{"locations":["New York, NY"],"sources":["Synthetic fixture"],"employment":["Full time"],"remote":["Hybrid","Remote"],"target_tracks":["People Analytics"]},
 }.items(): mocks.enter_context(patch.object(p,name,return_value=value))
 mocks.enter_context(patch.object(p,"_job_detail"))
 mocks.enter_context(patch("app.product_studio.dashboard_snapshot",return_value=(counts,[
  {"job_id":81002,"title":"Human Resources Operations Coordinator","company_name":"Example International Education and Research Foundation","display_status":"Needs review","updated_at":None},
  {"job_id":81003,"title":"Talent Acquisition Analyst","company_name":"Example Health Systems","display_status":"Needs input","updated_at":None},
 ])))
 p.dashboard()
'''


def preview_tree():
    app = AppTest.from_string(PREVIEW_SOURCE, default_timeout=20).run()
    if app.exception:
        raise RuntimeError(str(app.exception))
    return app


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _render(node) -> str:
    kind = node.type
    proto = node.proto
    if kind == "markdown":
        if node.allow_html:
            return '<div data-testid="stMarkdownContainer">' + node.value + '</div>'
        return '<p>' + _escape(node.value) + '</p>'
    if kind == "caption":
        return '<div data-testid="stCaptionContainer"><p>' + _escape(node.value) + '</p></div>'
    if kind == "metric":
        return '<div data-testid="stMetric"><div data-testid="stMetricLabel"><p>' + _escape(proto.label) + '</p></div><div data-testid="stMetricValue">' + _escape(proto.body) + '</div></div>'
    if kind in {"button", "link_button"}:
        key = getattr(node, "key", "") or ""
        label = _escape(proto.label)
        control = f'<button kind="{proto.type}" type="button">{label}</button>' if kind == "button" else f'<a kind="{proto.type}" href="{_escape(proto.url)}">{label}</a>'
        return f'<div class="st-key-{_escape(key)}"><div class="{"stButton" if kind == "button" else "stLinkButton"}">{control}</div></div>'
    if kind == "text_input":
        return '<div data-testid="stTextInput"><label>' + _escape(proto.label) + '<input placeholder="' + _escape(proto.placeholder) + '"></label></div>'
    if kind == "selectbox":
        return '<div><label>' + _escape(proto.label) + '<select><option>' + _escape(proto.options[0] if proto.options else "") + '</option></select></label></div>'
    children = getattr(node, "children", None)
    if children is not None:
        body = "".join(_render(child) for child in children.values())
        if kind == "expander":
            return '<details data-testid="stExpander"><summary>' + _escape(proto.label) + '</summary>' + body + '</details>'
        if kind == "column":
            return f'<div data-testid="stColumn" style="flex:{proto.weight} 1 0">{body}</div>'
        key = str(getattr(proto, "id", "") or "").split("-", 2)[-1]
        horizontal = kind == "flex_container" and proto.flex_container.direction == 2
        testid = "stHorizontalBlock" if horizontal else "stVerticalBlock"
        return f'<div class="st-key-{_escape(key)}" data-testid="{testid}">{body}</div>'
    return ""


def preview_html() -> str:
    body = _render(preview_tree().main)
    # Minimal adapters for native Streamlit elements, not a replacement frontend.
    adapters = '''<style>
    body{margin:0}button,input,select{font:inherit}button{padding:.65rem .8rem;width:100%;cursor:pointer}
    [data-testid="stHorizontalBlock"]{display:flex;gap:1rem;width:100%;align-items:stretch}
    [data-testid="stColumn"]{min-width:0}[data-testid="stVerticalBlock"]{display:flex;flex-direction:column;gap:1rem}
    [data-testid="stMarkdownContainer"]:has(>style){display:none}
    .stLinkButton a{display:block;padding:.65rem;text-align:center;text-decoration:none}
    label{display:grid;gap:.4rem;font-size:.85rem}input,select{padding:.7rem;border:1px solid #ced7d0;border-radius:10px;width:100%;background:white}
    details[data-testid="stExpander"]{padding:.75rem;background:#fff}summary{cursor:pointer;padding:.65rem}
    .preview-note{padding:.6rem 1rem;background:#123d31;color:white;font:13px sans-serif;text-align:center}
    @media(max-width:640px){.st-key-product_dashboard_grid [data-testid="stHorizontalBlock"]{flex-wrap:wrap}.st-key-product_dashboard_grid [data-testid="stColumn"]{flex-basis:100%!important}}
    </style>'''
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>MUNSHI · Synthetic component preview</title>' + adapters + '</head><body><div class="preview-note">Synthetic component preview · buttons do not execute actions · not a deployed dashboard</div><div class="stApp"><main data-testid="stMain"><div class="block-container">' + body + '</div></main></div></body></html>'


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/munshi-studio.html")
    target.write_text(preview_html(), encoding="utf-8")
    print(f"Synthetic component preview: {target}")
