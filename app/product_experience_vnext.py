"""Accessible responsive navigation and presentation for the original MUNSHI site.

No application operation, data mutation, provider execution or submit authority
is implemented here. Keep desktop/mobile links on the same canonical routes.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlencode

import streamlit as st

# Small first-party line icons; no external asset request or JavaScript needed.
_MOBILE_ICONS = {
    "dashboard": '<path d="m3 10 9-7 9 7"/><path d="M5 9v12h14V9"/><path d="M9 21v-7h6v7"/>',
    "jobs": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "prepare-application": '<rect x="4" y="4" width="16" height="17" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "auto-prepare": '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="5"/><path d="m10 12 1.5 1.5L14 11"/>',
    "tracker": '<path d="M4 19V6M10 19v-8M16 19V4M22 19v-6"/><path d="M2 21h21"/>',
}
PRIMARY_DESTINATIONS = (
    ("dashboard", "Home"),
    ("jobs", "Jobs"),
    ("prepare-application", "Applications"),
    ("auto-prepare", "Prepare"),
    ("tracker", "Tracker"),
)

ALLOWED_VIEWS = frozenset({
    "dashboard", "jobs", "prepare-application", "auto-prepare",
    "tracker", "profile", "resume-studio", "research", "settings",
})


def product_href(view: str, *, job_id: int | None = None) -> str:
    """Generate only local allowlisted deep links; never interpolate arbitrary URLs."""
    if view not in ALLOWED_VIEWS:
        raise ValueError("Unknown MUNSHI navigation route.")
    params: dict[str, str] = {"view": view}
    if job_id is not None:
        if view not in {"jobs", "prepare-application"}:
            raise ValueError("Job deep links are supported only for jobs and applications.")
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
            raise ValueError("Job ID must be a positive integer.")
        params["job"] = str(job_id)
    return "?" + urlencode(params)


def mobile_dock_markup(active: str) -> str:
    """Use ordinary links so mobile navigation survives Streamlit reruns."""
    anchors = []
    for route, label in PRIMARY_DESTINATIONS:
        selected = active == route
        current = ' aria-current="page"' if selected else ""
        active_class = " is-active" if selected else ""
        icon = (
            '<svg viewBox="0 0 24 24" aria-hidden="true" '
            'fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linejoin="round" stroke-linecap="round">'
            + _MOBILE_ICONS[route] + '</svg>'
        )
        anchors.append(
            f'<a class="munshi-dock-link{active_class}" '
            f'href="{escape(product_href(route), quote=True)}" target="_self"{current}>'
            f'<span class="munshi-dock-symbol" aria-hidden="true">{icon}</span>'
            f'<span class="munshi-dock-label">{escape(label)}</span></a>'
        )
    return '<nav class="munshi-mobile-dock" aria-label="Quick navigation">' + "".join(anchors) + "</nav>"


def application_breadcrumb_markup(job_id: int) -> str:
    """A real navigation path back to browse, application list and Candidate Truth."""
    app = escape(product_href("prepare-application"), quote=True)
    jobs = escape(product_href("jobs", job_id=job_id), quote=True)
    profile = escape(product_href("profile"), quote=True)
    return (
        '<nav class="munshi-breadcrumb" aria-label="Application shortcuts">'
        f'<a href="{app}" target="_self">← All applications</a>'
        f'<a href="{jobs}" target="_self">Job details</a>'
        f'<a href="{profile}" target="_self">My profile</a>'
        "</nav>"
    )


def lifecycle_markup() -> str:
    """Static, aspirational stages, never presented as completed actions or receipts."""
    steps = (
        ("01", "Prepare", "Build the job-specific package."),
        ("02", "Resolve", "Answer only questions that need you."),
        ("03", "Review once", "Confirm the exact resume and answers."),
        ("04", "Apply", "Run only with valid submission authority."),
        ("05", "Verify", "Record independent employer confirmation."),
    )
    parts = ['<ol class="munshi-journey" aria-label="Application process">']
    for number, title, description in steps:
        parts.append(
            '<li class="munshi-journey-step">'
            f'<span class="munshi-journey-number">{number}</span>'
            f'<strong>{escape(title)}</strong>'
            f'<span>{escape(description)}</span>'
            "</li>"
        )
    parts.append("</ol>")
    return "".join(parts)


def application_action_copy(canonical_state: str, existing: str) -> str:
    """Make the durable job state explanatory without inventing an action."""
    state = str(canonical_state or "").strip().upper()
    if state in {"QUEUED", "RUNNING", "PREPARING", "PREPARING_ATS"}:
        return "Preparing in background · no action needed yet"
    if state in {"READY_TO_SUBMIT", "SUBMITTING"}:
        return "Applying or verifying · do not resubmit"
    if state in {"READY_FOR_APPROVAL", "READY_FOR_REVIEW"}:
        return "Review the exact package and authorize once"
    return str(existing or "Open application")


def application_stage_markup(stage: int, *, receipt_verified: bool = False) -> str:
    """Status stages are projections, not execution permissions or receipt claims."""
    names = (
        "Job intelligence",
        "Resume tailoring",
        "Package checks",
        "Review",
        "Applying",
        "Employer verified" if receipt_verified else "Verify receipt",
    )
    step = max(0, min(6, int(stage)))
    parts = ['<ol class="munshi-stage-track" aria-label="Application progress">']
    for index, title in enumerate(names, 1):
        complete = index < step or (index == 6 and receipt_verified)
        current = index == step and not complete
        status = "is-complete" if complete else "is-current" if current else "is-pending"
        aria = ' aria-current="step"' if current else ""
        symbol = "✓" if complete else str(index).zfill(2)
        parts.append(
            f'<li class="munshi-stage {status}"{aria}>'
            f'<span class="munshi-stage-marker" aria-hidden="true">{symbol}</span>'
            f'<span>{escape(title)}</span></li>'
        )
    parts.append("</ol>")
    return "".join(parts)


def presentation_css() -> str:
    """Progressive layout overrides; preserve original crown/colors/theme."""
    return """<style>
    /* MUNSHI Product VNext · responsive, original theme, no JS or runtime hooks */
    :root{--mn-forest:#123d31;--mn-forest-dark:#0b3026;--mn-cream:#f6f7f4;
          --mn-brass:#9b6e2d;--mn-ink:#142129;--mn-muted:#52636a;
          --mn-border:#dfe5e0;--mn-surface:#fff}
    .munshi-skip-link{position:fixed;left:1rem;top:-80px;z-index:1100;
      padding:.7rem 1rem;background:#123d31;color:#fff!important;border-radius:12px}
    .munshi-skip-link:focus{top:1rem}
    .munshi-mobile-dock{display:none}
    .product-header{min-width:0;max-width:100%;gap:clamp(.55rem,1.4vw,1.4rem)}
    .product-nav-link{transition:background-color .15s ease,color .15s ease}
    .product-nav-link[aria-current="page"],.mobile-nav-link[aria-current="page"]{
      font-weight:800;text-decoration:none}
    .stApp [data-testid="stMarkdownContainer"] a:not(.stLinkButton a){
      text-underline-offset:3px}
    .stApp [data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;
      overflow-wrap:anywhere;font-size:clamp(1.25rem,2.3vw,2rem)}
    .stApp [data-testid="stCaptionContainer"]{line-height:1.5}
    .munshi-breadcrumb{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;
      margin:.4rem 0 1.15rem}
    .munshi-breadcrumb a{display:inline-flex;align-items:center;
      min-height:42px;border:1px solid var(--mn-border);border-radius:999px;
      padding:.45rem .8rem;background:var(--mn-surface);
      color:var(--mn-forest)!important;font-size:.85rem;font-weight:720;
      text-decoration:none!important}
    .munshi-breadcrumb a:hover{background:#e4efe9}
    .stApp ol.munshi-stage-track{list-style:none!important;padding:0!important;margin:1rem 0;
      display:grid!important;grid-template-columns:repeat(6,minmax(0,1fr))!important;
      width:100%;gap:.55rem}
    .munshi-stage{display:flex;gap:.45rem;align-items:center;
      min-width:0;padding:.65rem .55rem;border-radius:12px;
      border:1px solid var(--mn-border);background:#fff;
      color:var(--mn-muted);font-size:.79rem;font-weight:700}
    .munshi-stage-marker{flex:0 0 25px;height:25px;border-radius:50%;
      display:grid;place-items:center;background:#edf1ee;color:#60726a;
      font-size:.72rem;font-weight:850}
    .munshi-stage.is-complete{background:#eaf4ee;color:#123d31}
    .munshi-stage.is-complete .munshi-stage-marker{
      background:#123d31;color:#fff}
    .munshi-stage.is-current{border-color:#b9cabd;color:#123d31;
      box-shadow:0 0 0 2px rgba(18,61,49,.06)}
    .munshi-stage.is-current .munshi-stage-marker{background:#d4e6da;color:#123d31}
    .munshi-journey{padding:0;margin:.8rem 0 1rem;list-style:none;
      display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:.65rem}
    .munshi-journey-step{display:flex;flex-direction:column;gap:.45rem;
      padding:1rem;min-width:0;background:var(--mn-surface);
      border:1px solid var(--mn-border);border-radius:16px;min-height:136px}
    .munshi-journey-number{font-size:.75rem;color:var(--mn-brass);font-weight:800;
      letter-spacing:.14em}
    .munshi-journey-step strong{font-size:.92rem;color:var(--mn-ink)}
    .munshi-journey-step>span:last-child{font-size:.79rem;line-height:1.5;
      color:var(--mn-muted)}
    [data-testid="stDialog"] [role="dialog"]{max-height:100dvh;overflow-y:auto}
    .stApp [data-testid="stAlert"]{border-radius:16px}
    .stApp [data-testid="stButton"]>button{white-space:normal!important;
      text-wrap:balance;line-height:1.35}
    @media(max-width:1080px){
      .munshi-journey{grid-template-columns:repeat(3,minmax(0,1fr))}
      .product-header-actions{min-width:0}
    }
    @media(max-width:920px){
      .munshi-mobile-dock{
        display:grid;grid-template-columns:repeat(5,minmax(0,1fr));
        gap:0;position:fixed;z-index:1000;bottom:0;left:0;right:0;
        width:100%;height:calc(66px + env(safe-area-inset-bottom,0px));
        padding:4px max(8px,env(safe-area-inset-left,0px))
          env(safe-area-inset-bottom,0px);
        background:rgba(255,255,255,.97);border-top:1px solid #d5ded7;
        box-shadow:0 -7px 25px rgba(20,33,41,.09);
      }
      .munshi-dock-link{display:flex;align-items:center;justify-content:center;
        flex-direction:column;min-width:0;gap:2px;border-radius:12px;
        text-decoration:none!important;color:#52636a!important;
        font-size:.66rem;font-weight:750;min-height:55px;touch-action:manipulation}
      .munshi-dock-link.is-active{color:#123d31!important;
        background:#e4efe9}
      .munshi-dock-link:focus-visible{outline:3px solid #9b6e2d;outline-offset:1px}
      .munshi-dock-symbol{font-size:1.35rem;line-height:1.15;font-weight:650}
      .munshi-dock-symbol svg{display:block;width:23px;height:23px}
      .munshi-dock-label{overflow:hidden;white-space:nowrap;
        text-overflow:ellipsis;max-width:100%}
      /* Fixed dock needs a real last-scroll viewport gap on iOS Safari.
         Preserve the original palette and keep the last recovery button
         accessible above the dock and browser toolbar. */
      .stApp [data-testid="stMain"]{scroll-padding-bottom:calc(9rem + env(safe-area-inset-bottom,0px))}
      .stApp [data-testid="stMain"] .block-container{
        padding-bottom:calc(11rem + env(safe-area-inset-bottom,0px))!important}
      .product-header{gap:.55rem!important}
      .status-pill-product{max-width:145px;overflow:hidden;
        text-overflow:ellipsis}
    }
    @media(max-width:760px){
      .block-container{padding-left:clamp(.65rem,3vw,1.15rem)!important;
        padding-right:clamp(.65rem,3vw,1.15rem)!important;
        width:100%!important;max-width:100%!important}
      /* Streamlit columns must not preserve fixed desktop widths. */
      .stApp [data-testid="stMain"] [data-testid="stHorizontalBlock"]{
        flex-wrap:wrap!important;gap:.65rem!important}
      .stApp [data-testid="stMain"] [data-testid="stHorizontalBlock"]>
        [data-testid="stColumn"]{min-width:0!important;max-width:100%!important;
          width:100%!important;flex:1 1 100%!important}
      .stApp [data-testid="stMetric"]{min-height:80px!important;
        padding:.8rem!important}
      .stApp [data-testid="stVerticalBlock"]{min-width:0}
      .stApp [data-testid="stMarkdownContainer"]{overflow-wrap:anywhere}
      .stApp [data-testid="stMarkdownContainer"] table{display:block;
        max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
      .stApp [data-testid="stDataFrame"]{max-width:100%;overflow-x:auto}
      .stApp [data-testid="stForm"]{max-width:100%}
      /* On mobile, Close/Archive are navigation actions, not dark-on-dark
         disabled-looking controls. Keep the dark MUNSHI theme and readable
         text and use two compact touch targets. */
      .stApp [class*="st-key-application_visibility_controls_"]
        [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:.5rem!important}
      .stApp [class*="st-key-application_visibility_controls_"]
        [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{
          flex:1 1 0!important;width:50%!important;min-width:0!important}
      .stApp [class*="st-key-application_visibility_controls_"]
        [data-testid="stButton"]>button{
          min-height:48px!important;border-radius:12px!important;
          background:#121820!important;color:#f4f8f5!important;
          border:1px solid #354a47!important;font-weight:700!important}
      .stApp [class*="st-key-application_visibility_controls_"]
        [data-testid="stButton"]>button:is(:hover,:focus-visible){
          border-color:#9b6e2d!important;color:#fff!important}
      .stApp [data-testid="stTextInput"] input,
      .stApp [data-testid="stTextArea"] textarea{font-size:16px!important}
      .stApp [data-testid="stButton"]>button,
      .stApp [data-testid="stLinkButton"]>a,
      .stApp [data-testid="stFormSubmitButton"]>button{
        min-height:48px!important;max-width:100%;white-space:normal!important}
      .stApp [data-testid="stRadio"] label{min-height:42px;
        display:inline-flex;align-items:center}
      .stApp [data-testid="stSelectbox"]{min-width:0}
      .stApp .product-callout{flex-direction:column;align-items:stretch}
      .stApp .surface,.stApp .split-panel{padding:.9rem}
      /* Show all six milestones rather than hiding four behind a
         horizontal rail with no visible scroll affordance. */
      .stApp ol.munshi-stage-track{
        display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;
        grid-auto-flow:row;grid-auto-columns:auto;
        overflow-x:visible;scroll-snap-type:none;
        gap:.5rem;padding:.15rem 0 .5rem!important}
      .stApp ol.munshi-stage-track .munshi-stage{
        min-width:0;min-height:54px;overflow-wrap:anywhere;
        scroll-snap-align:none;line-height:1.3}
      .munshi-journey{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(210px,78%);
        grid-template-columns:none;overflow-x:auto;overscroll-behavior-x:contain;
        scroll-snap-type:x proximity;scroll-padding-inline:1rem;
        -webkit-overflow-scrolling:touch;padding:.12rem .15rem .65rem}
      .munshi-journey-step{min-height:126px;scroll-snap-align:start}
      .munshi-breadcrumb{gap:.35rem}
      .munshi-breadcrumb a{min-height:44px;flex:0 1 auto}
      .stApp [data-testid="stExpander"]{max-width:100%}
      .stApp [data-testid="stDialog"] [role="dialog"]{width:100vw!important;
        max-width:100vw!important;border-radius:0!important}
    }
    @media(max-width:420px){
      .product-header{min-width:0}
      .brand{font-size:.96rem!important}
      .status-pill-product{max-width:110px!important;font-size:.62rem!important}
      .munshi-dock-label{font-size:.6rem}
      .munshi-breadcrumb a{font-size:.75rem;padding:.4rem .65rem}
      .stApp [data-testid="stMetricLabel"] p{font-size:.77rem}
    }
    /* iPhone Safari: keep the existing palette, improve touch, safe areas and
       internal scrolling instead of shrinking the desktop canvas. */
    @media(max-width:920px){
      .st-key-product_top_bar{
        padding-top:max(.55rem,env(safe-area-inset-top,0px))!important;
        padding-left:max(.7rem,env(safe-area-inset-left,0px))!important;
        padding-right:max(.7rem,env(safe-area-inset-right,0px))!important}
      .mobile-nav{z-index:4}
      .mobile-nav[open] nav{
        z-index:5;max-height:min(68dvh,560px);overflow-y:auto;
        overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
      .mobile-nav-link{min-height:48px;white-space:normal}
      .munshi-mobile-dock{padding-right:max(8px,env(safe-area-inset-right,0px))}
    }
    @media(max-width:760px){
      .stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{
        min-width:0!important;max-width:100%!important}
      .stApp [data-testid="stMain"] [data-testid="stVerticalBlock"],
      .stApp [data-testid="stMain"] [data-testid="stElementContainer"]{
        min-width:0;max-width:100%}
      .stApp [data-testid="stTabs"] [role="tablist"]{
        display:flex;max-width:calc(100dvw - 1.4rem);overflow-x:auto;
        overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch;
        scrollbar-width:thin;flex-wrap:nowrap}
      .stApp [data-testid="stTabs"] [role="tab"]{
        flex:0 0 auto;min-height:44px;white-space:nowrap}
      .stApp [data-testid="stMarkdownContainer"] pre,
      .stApp [data-testid="stMarkdownContainer"] table{
        max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
      .stApp [data-testid="stDataFrame"],.stApp [data-testid="stTable"],
      .stApp [data-testid="stFileUploader"]{max-width:100%;min-width:0}
      .stApp [data-testid="stTextInput"] input,
      .stApp [data-testid="stNumberInput"] input,
      .stApp [data-testid="stDateInput"] input,
      .stApp [data-baseweb="select"] input,
      .stApp [data-baseweb="textarea"] textarea{
        font-size:16px!important}
      .stApp [data-testid="stDownloadButton"]>button,
      .stApp [data-testid="stNumberInput"] button{
        min-height:44px!important}
      .stApp [data-testid="stDialog"] [role="dialog"]{
        width:calc(100dvw - 1rem)!important;
        max-width:calc(100dvw - 1rem)!important;
        max-height:calc(100dvh - 1rem)!important;
        border-radius:16px!important;overflow-y:auto;
        -webkit-overflow-scrolling:touch}
      [data-baseweb="popover"],[role="listbox"]{
        max-width:calc(100dvw - 1rem);overflow-wrap:anywhere}
    }
    @media(max-width:420px){
      .product-header-actions{min-width:0;flex:0 1 auto}
      .status-pill-product{max-width:105px!important}
      .product-header .brand{flex:0 0 auto}
      .mobile-nav summary{min-width:58px}
    }
    @media(prefers-reduced-motion:reduce){
      .product-nav-link,.munshi-mobile-dock *{transition:none!important;
        animation:none!important}
    }
    </style>"""


def inject_presentation_css() -> None:
    st.markdown(presentation_css(), unsafe_allow_html=True)


def render_mobile_dock(active: str) -> None:
    st.markdown(mobile_dock_markup(active), unsafe_allow_html=True)


def render_application_breadcrumb(job_id: int) -> None:
    st.markdown(application_breadcrumb_markup(job_id), unsafe_allow_html=True)


def render_lifecycle_steps() -> None:
    st.markdown(lifecycle_markup(), unsafe_allow_html=True)
