"""Shared light-product presentation primitives for MUNSHI Apply."""
from __future__ import annotations

import hashlib
import html
from typing import Any

import streamlit as st


PASTELS = (
    "#DCEFE5",  # mint
    "#DCEAF6",  # powder blue
    "#F3DDE6",  # blush
    "#F3E8C8",  # sand
    "#E7DFF4",  # lavender
    "#D9EEF0",  # aqua
    "#F6E1D4",  # peach
    "#E4F0D8",  # sage
    "#F2E2D8",  # warm rose
    "#DDE7F2",  # cloud blue
    "#E6F4EC",  # soft jade
    "#EEE2F1",  # lilac mist
    "#EAF2D5",  # pistachio
    "#D9E8F7",  # sky
    "#F5DDD8",  # coral mist
    "#F4EED8",  # cream
    "#E2E4F5",  # periwinkle
    "#F0E4D2",  # warm oat
)


def esc(value: Any, fallback: str = "Not available") -> str:
    text = str(value or "").strip()
    return html.escape(text if text else fallback)


def pastel_for(value: Any) -> str:
    digest = hashlib.sha256(str(value or "unknown").encode("utf-8")).digest()
    return PASTELS[digest[0] % len(PASTELS)]


def product_css() -> str:
    return """
    <style>
    :root{color-scheme:light!important;--m-font:"Manrope","Avenir Next",Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--m-bg:#F6F7F4;--m-soft:#EFF2EE;--m-surface:#FFF;--m-ink:#142129;--m-secondary:#44545B;--m-muted:#66737B;--m-border:#DFE5E0;--m-strong:#CED7D0;--m-forest:#123D31;--m-forest-hover:#0B3026;--m-active:#E4EFE9;--m-brass:#9B6E2D;--m-danger:#A84249;--m-radius:18px}
    *{box-sizing:border-box}html,body,.stApp,[data-baseweb]{color-scheme:light!important}html,body,[class*="css"],.stApp{font-family:var(--m-font)!important}button,input,textarea,select,[role="button"],[data-baseweb]{font-family:var(--m-font)!important}html,body{overflow-x:hidden;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;font-feature-settings:"kern" 1,"liga" 1}
    .stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:var(--m-bg)!important;color:var(--m-ink)}[data-testid="stHeader"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],[data-testid="stSidebar"],button[kind="header"]{display:none!important}.block-container{width:100%!important;max-width:1680px!important;padding:0 2.25rem 4rem!important;margin:0 auto!important}
    h1,h2,h3{color:var(--m-ink)!important;letter-spacing:-.032em!important}h1{font-size:clamp(1.85rem,3vw,2.6rem)!important;line-height:1.08!important;font-weight:750!important;margin:.25rem 0 .65rem!important}h2{font-size:1.38rem!important;font-weight:700!important}h3{font-size:1.05rem!important;font-weight:700!important}p,[data-testid="stCaptionContainer"]{color:var(--m-secondary);letter-spacing:-.008em}
    .st-key-product_top_bar{width:100vw!important;max-width:none!important;margin:0 calc(50% - 50vw) 1.4rem!important;padding:.7rem max(1.25rem,calc((100vw - 1630px)/2))!important;min-height:66px;background:#FFF;border-bottom:1px solid var(--m-border);position:sticky;top:0;z-index:100}.product-header{min-height:50px;display:flex;align-items:center;gap:1.4rem}.brand{display:inline-flex;align-items:center;gap:.58rem;color:var(--m-forest)!important;text-decoration:none!important;font-size:1.12rem;font-weight:800;letter-spacing:-.04em;white-space:nowrap}.brand-mark{display:inline-grid;place-items:center;width:29px;height:29px;font-size:.9rem;font-weight:900;color:#FFF;background:var(--m-forest);clip-path:polygon(50% 0,100% 100%,72% 100%,50% 55%,28% 100%,0 100%)}
    .product-nav{display:flex;align-items:center;gap:.18rem;flex:1}.product-nav-link,.mobile-nav-link{display:inline-flex;align-items:center;min-height:40px;padding:.55rem .72rem;border-radius:11px;color:var(--m-secondary)!important;text-decoration:none!important;font-size:.88rem;font-weight:650;white-space:nowrap}.product-nav-link:hover,.mobile-nav-link:hover,.product-nav-link.active,.mobile-nav-link.active{background:var(--m-active);color:var(--m-forest)!important}.product-header-actions{display:flex;align-items:center;gap:.6rem;margin-left:auto}.status-pill-product{display:inline-flex;align-items:center;gap:.4rem;border-radius:999px;padding:.48rem .72rem;background:var(--m-forest);color:#FFF;font-size:.74rem;font-weight:720;white-space:nowrap}.status-pill-product:before{content:"";width:.42rem;height:.42rem;border-radius:50%;background:#D5C16B}.mobile-nav{display:none;position:relative}.mobile-nav summary{list-style:none;cursor:pointer;min-height:44px;display:grid;place-items:center;padding:.45rem .75rem;border:1px solid var(--m-strong);border-radius:999px;color:var(--m-forest);font-weight:720}.mobile-nav summary::-webkit-details-marker{display:none}.mobile-nav nav{position:absolute;right:0;top:calc(100% + .55rem);width:min(290px,calc(100vw - 2rem));padding:.55rem;display:grid;background:#FFF;border:1px solid var(--m-border);border-radius:15px;box-shadow:0 16px 36px rgba(20,33,41,.14)}
    .product-page{margin-top:.1rem}.page-kicker-product{font-size:.69rem;letter-spacing:.15em;text-transform:uppercase;color:var(--m-brass);font-weight:820}.page-copy-product{max-width:760px;color:var(--m-muted);font-size:.96rem;line-height:1.55;margin:0 0 1rem}.surface,.product-callout,.split-panel{background:var(--m-surface);border:1px solid var(--m-border);border-radius:var(--m-radius);padding:1.2rem}.product-callout{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:.8rem 0 1rem;box-shadow:0 8px 24px rgba(20,33,41,.035)}.product-callout strong{display:block;font-size:1rem}.product-callout span{display:block;color:var(--m-muted);margin-top:.15rem}
    [class*="st-key-product_"][class*="_search"]{background:#FFF;border:1px solid var(--m-border);border-radius:var(--m-radius);padding:1rem 1rem .8rem!important;box-shadow:0 8px 24px rgba(20,33,41,.045);margin:.45rem 0 1rem}[class*="st-key-product_"][class*="_search"] [data-testid="stTextInput"] input{font-size:1.04rem!important}.filter-label{font-size:.76rem;color:var(--m-muted);font-weight:720;margin:0 0 -.35rem .1rem}
    [data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea,[data-baseweb="select"]>div{border-radius:12px!important;border-color:var(--m-strong)!important;background:#FFF!important;color:var(--m-ink)!important}[data-testid="stTextInput"] input:focus,[data-testid="stTextArea"] textarea:focus{border-color:var(--m-brass)!important;box-shadow:0 0 0 2px rgba(155,110,45,.15)!important}.stButton>button,[data-testid="stFormSubmitButton"]>button,.stLinkButton>a{min-height:42px!important;border-radius:999px!important;border:1px solid var(--m-strong)!important;background:#FFF!important;color:var(--m-secondary)!important;box-shadow:0 1px 3px rgba(20,33,41,.06)!important;font-weight:690!important}.stButton>button[kind="primary"],[data-testid="stFormSubmitButton"]>button[kind="primary"],.stLinkButton>a[kind="primary"]{background:var(--m-forest)!important;border-color:var(--m-forest)!important;color:#FFF!important}.stButton>button[kind="primary"]:hover,[data-testid="stFormSubmitButton"]>button[kind="primary"]:hover{background:var(--m-forest-hover)!important}.stButton>button:focus-visible,.stLinkButton>a:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,[role="radio"]:focus-visible{outline:3px solid rgba(155,110,45,.38)!important;outline-offset:2px}
    [data-testid="stMetric"]{background:#FFF;border:1px solid var(--m-border);border-radius:15px;padding:.85rem 1rem;min-height:92px}[data-testid="stMetricLabel"]{color:var(--m-muted)!important}[data-testid="stMetricValue"]{color:var(--m-ink)!important;font-weight:760!important}.metric-strip{margin:.25rem 0 1rem}
    .score-ring{width:58px;height:58px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--m-forest) var(--score),rgba(20,33,41,.16) 0);flex:none}.score-ring-inner{display:grid;place-items:center;width:49px;height:49px;border-radius:50%;background:var(--card-bg,#FFF);font-size:.78rem;line-height:1.05;color:var(--m-ink);text-align:center}.score-ring-inner small{font-size:.55rem;color:var(--m-muted);letter-spacing:.06em}.job-card{overflow:hidden;background:transparent;height:100%;margin:0}.job-card-main{min-height:248px;padding:1.08rem 1.1rem;background:var(--card-bg);display:flex;flex-direction:column;border-radius:15px 15px 0 0}.job-top{display:flex;justify-content:space-between;gap:.65rem;color:var(--m-secondary);font-size:.82rem;overflow-wrap:anywhere}.job-title{font-size:clamp(1.12rem,1.5vw,1.34rem);line-height:1.2;font-weight:650;color:var(--m-ink);letter-spacing:-.028em;margin:1rem 0 .42rem;overflow-wrap:anywhere}.job-company{font-weight:700;color:var(--m-ink);margin-top:auto;overflow-wrap:anywhere;letter-spacing:-.018em}.job-meta{font-size:.79rem;font-weight:500;color:var(--m-muted);margin-top:.2rem;overflow-wrap:anywhere}.job-destination{display:grid;gap:.1rem;margin-top:.35rem;color:var(--m-muted);font-size:.72rem;line-height:1.35;overflow-wrap:anywhere;word-break:break-word}.job-destination span,.job-destination b{white-space:normal;overflow-wrap:anywhere}.tag{display:inline-block;padding:.23rem .48rem;border-radius:999px;background:rgba(255,255,255,.58);border:1px solid rgba(20,33,41,.08);font-size:.69rem;color:var(--m-secondary);margin:.4rem .25rem 0 0;max-width:100%;overflow-wrap:anywhere;white-space:normal}
    [class*="st-key-product_card_"]{position:relative!important;border:1px solid var(--m-border);border-radius:var(--m-radius);overflow:hidden;background:#FFF;height:100%;padding:0!important;box-shadow:0 5px 16px rgba(20,33,41,.045)}[class*="st-key-product_card_"] [data-testid="stHorizontalBlock"]{padding:.6rem .65rem .72rem;gap:.32rem!important}[class*="st-key-product_card_"] .stButton>button,[class*="st-key-product_card_"] .stLinkButton>a{font-size:.72rem!important;padding:.34rem .42rem!important;min-height:40px!important;height:auto!important;overflow:visible!important;text-overflow:clip!important;white-space:normal!important;line-height:1.2!important;overflow-wrap:anywhere!important}
    .section-row{display:flex;align-items:end;justify-content:space-between;gap:1rem;margin:1.25rem 0 .7rem}.section-row h2{margin:0!important}.quiet{color:var(--m-muted)!important}.table-shell{border:1px solid var(--m-border);border-radius:var(--m-radius);overflow:hidden;background:#FFF;padding:.35rem}.empty-product{border:1px dashed var(--m-strong);border-radius:var(--m-radius);padding:2.2rem 1rem;text-align:center;color:var(--m-muted);background:#FFF}.empty-product h3{margin-top:0!important}.muted-panel{background:var(--m-soft);border-radius:15px;padding:1.05rem}.lane-card{border:1px solid var(--m-border);border-radius:15px;padding:1rem;background:#FFF;margin-bottom:.55rem}.lane-card strong{color:var(--m-ink)}.evidence-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem 1rem;margin:.8rem 0}.evidence-item{font-size:.83rem;color:var(--m-secondary);overflow-wrap:anywhere}.evidence-item b{display:block;color:var(--m-muted);font-size:.67rem;letter-spacing:.06em;text-transform:uppercase;margin-bottom:.15rem}.status-chip{display:inline-flex;align-items:center;border:1px solid var(--m-border);border-radius:999px;padding:.24rem .52rem;font-size:.72rem;font-weight:720;color:var(--m-secondary);background:var(--m-soft)}.pipeline-row{display:grid;grid-template-columns:minmax(220px,2fr) minmax(140px,1fr) minmax(110px,.7fr);gap:1rem;align-items:center;padding:.9rem 1rem;border-bottom:1px solid var(--m-border)}.pipeline-row:last-child{border-bottom:0}.pipeline-row strong,.pipeline-row span{display:block;overflow-wrap:anywhere}.pipeline-meta{font-size:.78rem;color:var(--m-muted);margin-top:.18rem}.message-reader{min-height:360px}.message-reader-body{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--m-secondary);line-height:1.62}
    .settings-illustration{height:100%;min-height:360px;border-radius:20px;background:linear-gradient(155deg,#EDF3EF,#F7F4EC);display:grid;place-items:center;text-align:center;padding:2rem;color:var(--m-muted)}.artifact-card{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:1rem;border:1px solid var(--m-border);border-radius:15px;background:#FFF;margin-bottom:.65rem}.intelligence-card{height:100%;background:#FFF;border:1px solid var(--m-border);border-radius:var(--m-radius);padding:1.05rem}.intelligence-card .big{font-size:1.65rem;font-weight:780;color:var(--m-forest)}
    [data-testid="stRadio"] [role="radiogroup"]{gap:.35rem!important;flex-wrap:wrap!important}[data-testid="stRadio"] label{border-radius:999px;padding:.24rem .55rem!important;min-height:40px;background:#FFF;border:1px solid var(--m-border)}.st-key-product_settings_split [data-testid="stRadio"] label{width:100%;border-radius:12px;padding:.58rem .7rem!important}
    .st-key-product_job_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"],.st-key-product_dashboard_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"]{min-width:0!important}

    /* V2.1 interaction polish: clickable cards, modal backdrop, compact filters. */
    .job-card-click{display:block;color:inherit!important;text-decoration:none!important;border-radius:15px 15px 0 0;cursor:pointer;transition:transform .16s ease,box-shadow .16s ease}
    .job-card-click:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(20,33,41,.10)}
    .job-card-click:focus-visible{outline:3px solid rgba(155,110,45,.38)!important;outline-offset:3px}
    /* V2.6 local job-card hit area: no browser navigation. */
    [class*="st-key-job_open_"]{
        position:absolute!important;
        top:0!important;
        left:0!important;
        right:0!important;
        height:224px!important;
        z-index:4!important;
        margin:0!important;
        padding:0!important;
    }
    [class*="st-key-job_open_"] button{
        width:100%!important;
        height:224px!important;
        min-height:224px!important;
        border:0!important;
        border-radius:15px 15px 0 0!important;
        background:transparent!important;
        box-shadow:none!important;
        opacity:0!important;
        cursor:pointer!important;
    }
    [class*="st-key-product_card_"] .stButton>button{position:relative;z-index:3}
    .stButton>button[kind="primary"],[data-testid="stBaseButton-primary"],[data-testid="stBaseButton-primary"] *,.stButton>button[kind="primary"] *{color:#FFF!important}
    div[data-baseweb="modal"]{backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px)}
    [data-testid="stDialog"] [role="dialog"]{border-radius:22px!important;border:1px solid var(--m-border)!important;box-shadow:0 26px 80px rgba(20,33,41,.22)!important}
    [class*="st-key-product_"][class*="_search"] details{border:1px solid var(--m-border);border-radius:14px;padding:.15rem .65rem;background:var(--m-soft)}
    [class*="st-key-product_"][class*="_search"] details summary{color:var(--m-forest);font-weight:720}
    .pipeline-evidence{display:block;color:var(--m-muted);font-size:.68rem;margin-top:.2rem;overflow-wrap:anywhere}
    .st-key-product_master_resume{background:linear-gradient(145deg,#F4FAF6,#FFF);border:1px solid #CFE1D6!important;box-shadow:0 9px 28px rgba(20,61,49,.06)}

    /* Final browser/contrast hardening after the Sol visual rescue. */
    [data-testid="stWidgetLabel"],
    [data-testid="stCheckbox"] label,
    [data-testid="stRadio"] label{
        color:var(--m-secondary)!important
    }
    input::placeholder,
    textarea::placeholder{
        color:#87928D!important;
        opacity:1!important
    }
    [data-baseweb="select"] input::placeholder{
        color:#87928D!important;
        opacity:1!important
    }
    [data-testid="stAlert"]{
        border-radius:14px!important;
        border:1px solid var(--m-border)!important
    }
    [data-testid="stDataFrame"]{
        border:1px solid var(--m-border);
        border-radius:var(--m-radius);
        overflow:hidden;
        background:#FFF
    }
    /* Unified job-card visual language for interactive workspaces. */
    [data-testid="stVerticalBlockBorderWrapper"]{
        background:linear-gradient(145deg,#FFFFFF,#F9FBF8)!important;
        border:1px solid var(--m-border)!important;
        border-radius:var(--m-radius)!important;
        box-shadow:0 7px 22px rgba(20,33,41,.045)!important
    }
    [class*="st-key-munshi_surface_"]{
        background:linear-gradient(145deg,#FFFFFF,#F9FBF8)!important;
        border:1px solid var(--m-border)!important;
        border-radius:var(--m-radius)!important;
        padding:1rem!important;
        box-shadow:0 7px 22px rgba(20,33,41,.045)!important
    }
    [class*="st-key-munshi_tint_"]{
        background:linear-gradient(145deg,#EDF7F1,#F9FBF8)!important;
        border:1px solid #CFE1D6!important;
        border-radius:var(--m-radius)!important;
        padding:1rem!important;
        box-shadow:0 7px 22px rgba(20,61,49,.05)!important
    }
    .st-key-product_auto_split > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child{
        background:linear-gradient(145deg,#FFFFFF,#F9FBF8)!important;
        border:1px solid var(--m-border)!important;
        border-radius:var(--m-radius)!important;
        padding:1rem!important;
        box-shadow:0 7px 22px rgba(20,33,41,.045)!important
    }

    /* Keep BaseWeb controls inside the light MUNSHI product theme. */
    [data-testid="stExpander"]{
        background:#FFF!important;
        border:1px solid var(--m-border)!important;
        border-radius:15px!important;
        overflow:hidden!important
    }
    [data-testid="stExpander"] summary{
        background:#F7F9F6!important;
        color:var(--m-forest)!important;
        min-height:48px!important
    }
    [data-testid="stExpander"] summary *{color:var(--m-forest)!important}
    [data-testid="stNumberInput"] input{
        background:#FFF!important;
        color:var(--m-ink)!important;
        border-color:var(--m-strong)!important
    }
    [data-testid="stNumberInput"] button{
        background:#F7F9F6!important;
        color:var(--m-secondary)!important;
        border-color:var(--m-border)!important
    }
    [data-baseweb="popover"],
    [data-baseweb="menu"],
    [role="listbox"]{
        background:#FFF!important;
        color:var(--m-ink)!important;
        border-color:var(--m-border)!important
    }
    [role="option"]{background:#FFF!important;color:var(--m-ink)!important}
    [role="option"]:hover,[role="option"][aria-selected="true"]{
        background:var(--m-active)!important;
        color:var(--m-forest)!important
    }
    .stButton>button:disabled,
    [data-testid="stFormSubmitButton"]>button:disabled,
    .stDownloadButton>button:disabled{
        background:#F1F3F1!important;
        color:#89938E!important;
        border-color:#E0E5E1!important;
        opacity:1!important
    }

    .mobile-nav[open] summary{
        background:var(--m-active);
        color:var(--m-forest)
    }
    .mobile-nav-link{
        width:100%;
        justify-content:flex-start
    }
    @media (max-width:430px){
        .status-pill-product{
            max-width:122px;
            overflow:hidden;
            text-overflow:ellipsis
        }
        .product-header{min-width:0}
        .brand{min-width:0}
        .mobile-nav{flex:none}
    }

    @media (max-width:1180px){.product-header{gap:.8rem}.product-nav-link{padding:.5rem .48rem;font-size:.8rem}.st-key-product_job_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"]){flex-wrap:wrap!important}.st-key-product_job_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"]{flex:1 1 calc(33.333% - 1rem)!important;min-width:240px!important}.st-key-product_dashboard_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"]){flex-wrap:wrap!important}.st-key-product_dashboard_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"]{flex:1 1 calc(50% - 1rem)!important;min-width:250px!important}}
    @media (max-width:920px){.product-nav{display:none}.mobile-nav{display:block}.product-header-actions>.product-nav-link{display:none}.st-key-product_job_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"]{flex-basis:calc(50% - .75rem)!important}.st-key-product_settings_split [data-testid="stHorizontalBlock"],.st-key-product_auto_split [data-testid="stHorizontalBlock"],.st-key-product_inbox_split [data-testid="stHorizontalBlock"],.st-key-product_dashboard_metrics [data-testid="stHorizontalBlock"],.st-key-product_auto_metrics [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}.st-key-product_settings_split [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"],.st-key-product_auto_split [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"],.st-key-product_inbox_split [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{flex:1 1 100%!important;min-width:0!important}.st-key-product_dashboard_metrics [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"],.st-key-product_auto_metrics [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{flex:1 1 calc(50% - .5rem)!important;min-width:150px!important}.settings-illustration{min-height:220px}}
    @media (max-width:640px){.block-container{padding:0 1rem 2.5rem!important}.st-key-product_top_bar{margin-bottom:1rem!important;padding:.55rem 1rem!important}.product-header{gap:.55rem}.brand{font-size:.98rem}.brand-mark{width:27px;height:27px}.product-header-actions{margin-left:auto}.status-pill-product{font-size:.65rem;padding:.42rem .55rem}.mobile-nav summary{font-size:.78rem;padding:.35rem .65rem}.page-copy-product{font-size:.9rem}.section-row{align-items:flex-start;flex-direction:column;gap:.15rem}.product-callout{align-items:flex-start;flex-direction:column}.job-card-main{min-height:220px}.stButton>button,.stLinkButton>a{min-height:44px!important}.st-key-product_job_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"]),.st-key-product_dashboard_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"]),.st-key-product_tracker_filters [data-testid="stHorizontalBlock"],[class*="st-key-product_"][class*="_search"] [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}.st-key-product_job_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"],.st-key-product_dashboard_grid div[data-testid="stHorizontalBlock"]:has([class*="st-key-product_card_"])>div[data-testid="stColumn"],.st-key-product_tracker_filters [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"],[class*="st-key-product_"][class*="_search"] [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{flex:1 1 100%!important;min-width:0!important}[class*="st-key-product_actions_"] [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}[class*="st-key-product_actions_"] [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{flex:1 1 calc(50% - .35rem)!important;min-width:0!important}[class*="st-key-product_actions_"] [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]:last-child{flex-basis:100%!important}.st-key-product_dashboard_metrics [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"],.st-key-product_auto_metrics [data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{flex-basis:100%!important;min-width:0!important}.evidence-list{grid-template-columns:1fr}.pipeline-row{grid-template-columns:1fr;gap:.45rem}.artifact-card{align-items:flex-start;flex-direction:column}}
    @media (prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
    </style>
    """


def inject_css() -> None:
    # Older profile/settings renderers reinject the base layer. Keep the final
    # shared system attached there too, rather than reverting the page mid-run.
    from app.product_studio import shell_css

    # Streamlit sends style-only HTML to its event container, so CSS does not
    # create document slots or wait for Markdown parsing/lazy renderer modules.
    st.html(shell_css())


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
