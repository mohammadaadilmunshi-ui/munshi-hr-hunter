"""Shared light-product presentation primitives for MUNSHI Apply."""
from __future__ import annotations

import hashlib
import html
from typing import Any

import streamlit as st


PASTELS = ("#DDF1FB", "#DDF8EA", "#FCE9CF", "#FBE2E4", "#FFF2C7", "#EBE6FB")


def esc(value: Any, fallback: str = "Not available") -> str:
    text = str(value or "").strip()
    return html.escape(text if text else fallback)


def pastel_for(value: Any) -> str:
    digest = hashlib.sha256(str(value or "unknown").encode("utf-8")).digest()
    return PASTELS[digest[0] % len(PASTELS)]


def product_css() -> str:
    return """
    <style>
    :root{color-scheme:light!important;--m-bg:#F7F8F6;--m-soft:#F2F4F1;--m-surface:#FFF;--m-ink:#142129;--m-secondary:#4D5B63;--m-muted:#7A8490;--m-border:#E2E7E3;--m-strong:#D3DAD5;--m-forest:#123D31;--m-forest-hover:#0D3127;--m-active:#E7F1EC;--m-brass:#B78A4A;--m-danger:#B04C50;--m-radius:17px}
    html,body,.stApp,[data-baseweb]{color-scheme:light!important} html,body,[class*="css"],.stApp{font-family:"Avenir Next",Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif!important}
    .stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:var(--m-bg)!important;color:var(--m-ink)} [data-testid="stHeader"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important}
    [data-testid="stSidebar"],button[kind="header"]{display:none!important}.block-container{max-width:none!important;padding:0 2.2rem 4rem!important}
    h1,h2,h3{color:var(--m-ink)!important;letter-spacing:-.03em!important} h1{font-size:2rem!important;font-weight:750!important} h2{font-size:1.42rem!important;font-weight:720!important} h3{font-size:1.06rem!important;font-weight:700!important}
    p,[data-testid="stCaptionContainer"]{color:var(--m-secondary)}
    .st-key-product_top_bar{margin:0 -2.2rem 1.8rem!important;padding:.85rem 2.2rem!important;min-height:70px;background:#fff;border-bottom:1px solid var(--m-border)}.brand{display:flex;align-items:center;gap:.62rem;white-space:nowrap;color:var(--m-forest);font-size:1.13rem;font-weight:760;letter-spacing:-.04em}.brand-mark{display:inline-grid;place-items:center;width:27px;height:27px;font-size:1rem;font-weight:900;color:#fff;background:var(--m-forest);clip-path:polygon(50% 0,100% 100%,72% 100%,50% 55%,28% 100%,0 100%)}
    .nav-spacer{height:8px}.status-pill-product{display:inline-flex;align-items:center;gap:.35rem;border-radius:999px;padding:.45rem .75rem;background:var(--m-forest);color:#fff;font-size:.78rem;font-weight:700;white-space:nowrap}.status-pill-product:before{content:"";width:.42rem;height:.42rem;border-radius:50%;background:#D5C16B}
    .nav-button button,.settings-nav button{min-height:39px!important;border:0!important;border-radius:13px!important;background:transparent!important;color:var(--m-secondary)!important;box-shadow:none!important;font-weight:650!important;padding:.48rem .67rem!important;white-space:nowrap}.nav-button button:hover,.settings-nav button:hover{background:var(--m-active)!important;color:var(--m-forest)!important}.nav-button.active button,.settings-nav .active button{background:var(--m-active)!important;color:var(--m-forest)!important}
    .product-page{margin-top:.25rem}.page-kicker-product{font-size:.68rem;letter-spacing:.13em;text-transform:uppercase;color:var(--m-brass);font-weight:800}.page-copy-product{max-width:780px;color:var(--m-muted);font-size:.98rem;line-height:1.55;margin-top:-.35rem;margin-bottom:1.35rem}
    .surface,.product-callout{background:var(--m-surface);border:1px solid var(--m-border);border-radius:var(--m-radius);padding:1.25rem}.product-callout{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:1.15rem 0 1.65rem}.product-callout strong{display:block;font-size:1rem}.product-callout span{display:block;color:var(--m-muted);margin-top:.2rem}
    .search-shell{background:#fff;border:1px solid var(--m-border);box-shadow:0 2px 5px rgba(20,33,41,.06);border-radius:17px;padding:.4rem .85rem;margin-bottom:.65rem}.search-shell input{border:0!important;box-shadow:none!important;font-size:1.07rem!important}.exclude-shell input{background:var(--m-soft)!important;border-color:var(--m-border)!important}
    [data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea,[data-baseweb="select"]>div{border-radius:12px!important;border-color:var(--m-strong)!important;background:#fff!important;color:var(--m-ink)!important}.stButton>button,[data-testid="stFormSubmitButton"]>button{min-height:40px!important;border-radius:999px!important;border:1px solid var(--m-strong)!important;background:#fff!important;color:var(--m-secondary)!important;box-shadow:0 1px 3px rgba(20,33,41,.07)!important;font-weight:680!important}.stButton>button[kind="primary"],[data-testid="stFormSubmitButton"]>button[kind="primary"]{background:var(--m-forest)!important;border-color:var(--m-forest)!important;color:#fff!important}.stButton>button[kind="primary"]:hover{background:var(--m-forest-hover)!important}.stButton>button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid rgba(183,138,74,.35)!important;outline-offset:2px}
    .filter-label{font-size:.75rem;color:var(--m-muted);font-weight:700;margin:0 0 -.45rem .1rem}.score-ring{width:58px;height:58px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--m-forest) var(--score),rgba(20,33,41,.18) 0);flex:none}.score-ring-inner{display:grid;place-items:center;width:49px;height:49px;border-radius:50%;background:var(--card-bg,#fff);font-size:.78rem;line-height:1.05;color:var(--m-ink);text-align:center}.score-ring-inner small{font-size:.56rem;color:var(--m-muted);letter-spacing:.05em}
    .job-card{border:1px solid var(--m-border);border-radius:var(--m-radius);overflow:hidden;background:#fff;height:100%;margin-bottom:.85rem}.job-card-main{min-height:245px;padding:1.1rem 1.15rem;background:var(--card-bg);display:flex;flex-direction:column}.job-top{display:flex;justify-content:space-between;gap:.7rem;color:var(--m-secondary);font-size:.82rem}.job-title{font-size:1.35rem;line-height:1.16;font-weight:720;color:var(--m-ink);letter-spacing:-.035em;margin:1.2rem 0 .5rem}.job-company{font-weight:720;color:var(--m-ink);margin-top:auto}.job-meta{font-size:.79rem;color:var(--m-muted);margin-top:.23rem}.tag{display:inline-block;padding:.24rem .5rem;border-radius:999px;background:rgba(255,255,255,.53);border:1px solid rgba(20,33,41,.08);font-size:.7rem;color:var(--m-secondary);margin:.48rem .3rem 0 0}.job-card-foot{padding:.7rem 1rem;border-top:1px solid rgba(20,33,41,.07);background:#fff}
    .section-row{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:1.5rem 0 .75rem}.section-row h2{margin:0!important}.quiet{color:var(--m-muted)!important}.table-shell{border:1px solid var(--m-border);border-radius:var(--m-radius);overflow:hidden;background:#fff;padding:.5rem}.empty-product{border:1px dashed var(--m-strong);border-radius:var(--m-radius);padding:3rem 1rem;text-align:center;color:var(--m-muted);background:#fff}.split-panel{background:#fff;border:1px solid var(--m-border);border-radius:var(--m-radius);padding:1.25rem;height:100%}.muted-panel{background:var(--m-soft);border-radius:var(--m-radius);padding:1.2rem}.lane-card{border:1px solid var(--m-border);border-radius:14px;padding:1rem;background:#fff;margin-bottom:.65rem}.lane-card strong{color:var(--m-ink)}
    .settings-illustration{height:100%;min-height:370px;border-radius:20px;background:var(--m-soft);display:grid;place-items:center;text-align:center;padding:2rem;color:var(--m-muted)}.artifact-page{width:min(100%,780px);min-height:520px;background:#fff;border:1px solid var(--m-border);box-shadow:0 12px 30px rgba(20,33,41,.08);padding:3rem;margin:1rem auto}.artifact-page h2{border-bottom:1px solid var(--m-ink);padding-bottom:.5rem}.artifact-line{height:9px;background:var(--m-soft);border-radius:99px;margin:.65rem 0}.artifact-line.short{width:58%}
    @media (max-width:720px){.block-container{padding:0 1rem 2.5rem!important}.st-key-product_top_bar{margin:0 -1rem 1rem!important;padding:.65rem 1rem!important;min-height:60px}.brand{font-size:.95rem}.nav-button button{font-size:.76rem!important;padding:.35rem .42rem!important}.status-pill-product{font-size:.66rem;padding:.35rem .5rem}.product-callout{align-items:flex-start;flex-direction:column}.job-card-main{min-height:215px}.artifact-page{padding:1.5rem;min-height:400px}.settings-illustration{min-height:210px}.stButton>button{min-height:44px!important}}
    </style>
    """


def inject_css() -> None:
    st.markdown(product_css(), unsafe_allow_html=True)


def page_intro(kicker: str, title: str, copy: str) -> None:
    st.markdown(f'<div class="product-page"><div class="page-kicker-product">{esc(kicker)}</div><h1>{esc(title)}</h1><div class="page-copy-product">{esc(copy)}</div></div>', unsafe_allow_html=True)


def score_ring(score: Any, background: str) -> str:
    try:
        value = max(0, min(100, round(float(score))))
    except (ValueError, TypeError):
        return '<div class="score-ring" style="--score:0deg;--card-bg:%s"><div class="score-ring-inner"><small>SCORE</small><br>—</div></div>' % background
    return f'<div class="score-ring" aria-label="{value}% match" style="--score:{value * 3.6:.1f}deg;--card-bg:{background}"><div class="score-ring-inner"><b>{value}%</b><small>MATCH</small></div></div>'


def safe_link(url: Any) -> str:
    value = str(url or "").strip()
    return value if value.startswith(("https://", "http://")) else ""
