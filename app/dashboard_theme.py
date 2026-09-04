from __future__ import annotations


# Canonical design tokens. All component styling below consumes this system.
DESIGN_TOKENS = {
    "background": "#F7F8F6", "surface": "#FFFFFF", "surface_elevated": "#FFFFFF",
    "sidebar": "#FFFFFF", "hero": "#F0F6F3", "border": "#E2E7E3",
    "border_strong": "#D3DAD5", "text_primary": "#142129",
    "text_secondary": "#4D5B63", "text_muted": "#7A8490", "accent": "#B78A4A",
    "success": "#237256", "warning": "#9A6918", "error": "#B04C50",
}


def premium_dashboard_css() -> str:
    """Return the browser-scheme-independent executive design system."""
    return """
    <style>
    :root {
      color-scheme:light!important;
      --ahh-background:#F7F8F6; --ahh-background-deep:#F2F4F1;
      --ahh-surface:#FFFFFF; --ahh-surface-elevated:#FFFFFF; --ahh-surface-muted:#F2F4F1;
      --ahh-sidebar:#FFFFFF; --ahh-sidebar-elevated:#F7F8F6; --ahh-hero:#F0F6F3;
      --ahh-border:#E2E7E3; --ahh-border-strong:#D3DAD5;
      --ahh-text-primary:#142129; --ahh-text-secondary:#4D5B63; --ahh-text-muted:#7A8490;
      --ahh-text-inverse:#142129; --ahh-accent:#B78A4A; --ahh-accent-soft:#E7F1EC;
      --ahh-accent-bg:#F5EEE3; --ahh-success:#237256; --ahh-success-bg:#E7F1EC;
      --ahh-warning:#9A6918; --ahh-warning-bg:#F9EFD9; --ahh-error:#B04C50;
      --ahh-error-bg:#F8E7E5; --ahh-info:#2E5F7B; --ahh-info-bg:#E6EFF4;
      --ahh-disabled:#89939C; --ahh-focus:rgba(173,129,66,.24);
      --ahh-shadow-sm:0 2px 9px rgba(8,23,34,.055);
      --ahh-shadow-md:0 14px 34px rgba(8,23,34,.09);
      --ahh-shadow-lg:0 24px 54px rgba(8,23,34,.15);
      --ahh-radius-sm:8px; --ahh-radius-md:14px; --ahh-radius-lg:22px;
      --ahh-space-1:.375rem; --ahh-space-2:.625rem; --ahh-space-3:1rem;
      --ahh-space-4:1.5rem; --ahh-space-5:2.25rem; --ahh-space-6:3.25rem;
      --ahh-font:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      --ahh-display:"Avenir Next",Inter,ui-sans-serif,-apple-system,sans-serif;
    }
    html,body,.stApp,[data-baseweb]{color-scheme:light!important}
    html,body,[class*="css"],.stApp{font-family:var(--ahh-font)}
    .stApp{color:var(--ahh-text-primary);background:var(--ahh-background)}
    [data-testid="stAppViewContainer"],[data-testid="stMain"]{background:transparent}
    [data-testid="stHeader"]{height:2.8rem;background:rgba(244,241,233,.76);backdrop-filter:blur(12px)}
    [data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important}
    /* Keep Streamlit 1.58 sidebar controls reachable while preserving the executive chrome. */
    [data-testid="stToolbar"]{background:transparent!important;box-shadow:none!important}
    [data-testid="stHeader"] [data-testid="stToolbar"] button{opacity:.72;transition:opacity .16s ease}
    [data-testid="stHeader"] [data-testid="stToolbar"] button:hover{opacity:1}
    [data-testid="stSidebarNav"]{display:none!important}
    .block-container{max-width:1520px;padding:3.1rem 2.15rem 5rem}
    h1,h2,h3,h4,h5,h6{color:var(--ahh-text-primary)!important;font-family:var(--ahh-display);letter-spacing:-.025em}
    h1{font-size:clamp(2rem,3vw,3rem)!important;font-weight:720!important} h2{font-weight:680!important} h3,h4{font-weight:650!important}
    p,li,label,[data-testid="stCaptionContainer"]{line-height:1.55} p,li{color:var(--ahh-text-secondary)}
    a{color:var(--ahh-info);text-underline-offset:.18em}

    /* Dark executive sidebar and grouped navigation. */
    [data-testid="stSidebar"]{background:radial-gradient(circle at 10% 0%,rgba(173,129,66,.17),transparent 20rem),var(--ahh-sidebar);border-right:1px solid rgba(231,214,184,.18);box-shadow:12px 0 36px rgba(8,23,34,.08)}
    [data-testid="stSidebar"]>div{padding-top:1.05rem} [data-testid="stSidebar"] *{color:var(--ahh-text-inverse)}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"]{color:#B7C2CA!important}
    [data-testid="stSidebar"] hr{border-color:rgba(231,214,184,.16)}
    [data-testid="stSidebar"] button{width:100%;min-height:2.38rem;justify-content:flex-start;color:#E8EDF0!important;background:transparent!important;border:1px solid transparent!important;border-radius:9px!important;padding:.48rem .72rem!important;font-size:.87rem!important;font-weight:570!important;box-shadow:none!important}
    [data-testid="stSidebar"] button:hover{color:#FFF!important;background:rgba(255,255,255,.065)!important;border-color:rgba(231,214,184,.2)!important;transform:translateX(2px)}
    [data-testid="stSidebar"] button[kind="primary"],[data-testid="stSidebar"] [data-testid*="stBaseButton-primary"]{color:#FFF!important;background:linear-gradient(90deg,rgba(173,129,66,.23),rgba(255,255,255,.065))!important;border-color:rgba(231,214,184,.38)!important;box-shadow:inset 3px 0 0 var(--ahh-accent)!important}
    .sidebar-brand{padding:.3rem .2rem .95rem}.sidebar-brand-row{display:flex;align-items:center;gap:.72rem}
    .sidebar-mark{display:grid;place-items:center;width:2.35rem;height:2.35rem;border:1px solid rgba(231,214,184,.55);border-radius:50%;color:var(--ahh-accent-soft);font-family:var(--ahh-display);font-size:.72rem;font-weight:800;letter-spacing:.08em;background:rgba(255,255,255,.045)}
    .sidebar-title{color:#FFF;font-family:var(--ahh-display);font-size:.98rem;font-weight:760;letter-spacing:.01em}
    .sidebar-subtitle{color:#9EADB7;font-size:.65rem;letter-spacing:.13em;text-transform:uppercase;margin-top:.12rem}
    .nav-group{color:#9CABB5;font-size:.61rem;font-weight:780;letter-spacing:.15em;text-transform:uppercase;margin:1rem .25rem .2rem}
    .sidebar-foot{margin-top:1rem;padding:.9rem .2rem 0;border-top:1px solid rgba(231,214,184,.14)}
    .sidebar-foot p{color:#9DABB5!important;font-size:.72rem;line-height:1.5}

    /* Hero and compact masthead. */
    .hero{position:relative;overflow:hidden;border:1px solid rgba(231,214,184,.34);border-radius:var(--ahh-radius-lg);padding:clamp(1.6rem,3vw,2.65rem);margin:.5rem 0 var(--ahh-space-5);color:var(--ahh-text-inverse);background:radial-gradient(circle at 91% 11%,rgba(173,129,66,.24),transparent 25rem),linear-gradient(132deg,#071824,var(--ahh-hero) 58%,#173445);box-shadow:var(--ahh-shadow-lg)}
    .hero::after{content:"";position:absolute;inset:auto 0 0;height:3px;background:linear-gradient(90deg,var(--ahh-accent),rgba(173,129,66,0))}
    .brand-row{display:flex;align-items:center;gap:.9rem;margin-bottom:1.4rem}.monogram{display:grid;place-items:center;width:2.55rem;height:2.55rem;border:1px solid rgba(231,214,184,.58);border-radius:50%;color:var(--ahh-accent-soft);font-family:var(--ahh-display);font-size:.75rem;font-weight:800;letter-spacing:.08em;background:rgba(255,255,255,.04)}
    .eyebrow,.page-kicker{color:var(--ahh-accent);text-transform:uppercase;letter-spacing:.16em;font-weight:800;font-size:.66rem}.hero .eyebrow{color:var(--ahh-accent-soft)}
    .hero h1{color:#FFF!important;margin:.05rem 0 .35rem!important}.hero-copy{color:#FFF;max-width:920px;font-family:var(--ahh-display);font-size:clamp(1.08rem,2vw,1.38rem);font-weight:650;line-height:1.45}.hero-subcopy{color:#C6D2D9;max-width:920px;font-size:.9rem;line-height:1.65;margin-top:.45rem}
    .hero-meta{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1.35rem}.hero-chip{border:1px solid rgba(231,214,184,.28);border-radius:999px;padding:.37rem .72rem;color:#E8ECEE;background:rgba(255,255,255,.045);font-size:.74rem;font-weight:650}
    .hero-chip.live::before{content:"";display:inline-block;width:.42rem;height:.42rem;border-radius:50%;background:#65B18E;margin-right:.43rem;box-shadow:0 0 0 3px rgba(101,177,142,.14)}
    .masthead{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:.2rem 0 1.2rem;padding:.78rem 1rem;border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.64);box-shadow:var(--ahh-shadow-sm)}
    .masthead-brand{color:var(--ahh-text-primary);font-family:var(--ahh-display);font-size:.78rem;font-weight:800;letter-spacing:.11em}.masthead-context{color:var(--ahh-text-muted);font-size:.74rem}
    .page-intro{margin:.85rem 0 1.25rem;padding-bottom:1rem;border-bottom:1px solid var(--ahh-border)}
    .page-intro h2{margin:.2rem 0 .15rem!important;font-size:clamp(1.55rem,2.3vw,2rem)!important}.page-copy{color:var(--ahh-text-muted);max-width:860px;margin-top:.25rem;font-size:.91rem}
    .section-heading{margin:1.65rem 0 .75rem}.section-heading strong{color:var(--ahh-text-primary);font-family:var(--ahh-display);font-size:1rem}.section-heading span{display:block;color:var(--ahh-text-muted);font-size:.8rem;margin-top:.14rem}

    /* Cards and semantic states. */
    [data-testid="stMetric"]{min-height:112px;height:100%;padding:.95rem 1rem;border:1px solid var(--ahh-border);border-top:2px solid var(--ahh-accent-soft);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.82);box-shadow:var(--ahh-shadow-sm);overflow:visible}
    [data-testid="stMetricLabel"],[data-testid="stMetricLabel"]>div,[data-testid="stMetricLabel"] p{color:#596B7B!important;min-height:2.1rem;height:auto!important;white-space:normal!important;overflow:visible!important;text-overflow:clip!important;overflow-wrap:anywhere;font-size:.72rem!important;line-height:1.35!important;letter-spacing:.06em;text-transform:uppercase;font-weight:760}
    [data-testid="stMetricValue"],[data-testid="stMetricValue"]>div{color:var(--ahh-text-primary)!important;font-family:var(--ahh-display);font-size:clamp(1.2rem,1.7vw,1.65rem)!important;font-weight:720!important;line-height:1.15!important;white-space:normal!important;overflow:visible!important;text-overflow:clip!important;overflow-wrap:anywhere}
    [data-testid="stMetric"] button{color:var(--ahh-text-secondary)!important}
    .snapshot-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem;margin:.8rem 0 1.25rem}
    .snapshot-card,.integration-card,.policy-card{border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.78);box-shadow:var(--ahh-shadow-sm)}
    .snapshot-card{padding:1rem 1.05rem}.snapshot-label{color:var(--ahh-text-muted);text-transform:uppercase;letter-spacing:.08em;font-size:.64rem;font-weight:800}.snapshot-value{color:var(--ahh-text-primary);font-family:var(--ahh-display);font-size:1.05rem;font-weight:700;margin-top:.32rem}.snapshot-detail{color:var(--ahh-text-muted);font-size:.76rem;line-height:1.45;margin-top:.28rem}
    .policy-card{min-height:144px;height:100%;padding:1.05rem 1.15rem;border-top:3px solid var(--ahh-accent)}.policy-card strong{color:var(--ahh-accent);font-size:.68rem;letter-spacing:.12em}.policy-card h2,.policy-card h3{margin:.52rem 0!important}.quiet{color:var(--ahh-text-muted);font-size:.79rem;line-height:1.5}
    .status-pill{display:inline-flex;align-items:center;max-width:100%;border-radius:999px;padding:.34rem .68rem;margin:.18rem .2rem .18rem 0;font-size:.7rem;line-height:1.25;font-weight:740;border:1px solid;white-space:normal}.status-pill.good{color:var(--ahh-success);background:var(--ahh-success-bg);border-color:#B8D8C9}.status-pill.warn{color:var(--ahh-warning);background:var(--ahh-warning-bg);border-color:#E1C995}.status-pill.bad{color:var(--ahh-error);background:var(--ahh-error-bg);border-color:#E2B5B1}.status-pill.neutral{color:var(--ahh-text-secondary);background:var(--ahh-surface-muted);border-color:var(--ahh-border)}
    [data-testid="stSidebar"] .status-pill{color:#EAF0F3;background:rgba(255,255,255,.05)}
    .adapter-truth-panel{display:grid;grid-template-columns:1.1fr 2.1fr;gap:1rem;align-items:stretch;padding:1.15rem;margin:1rem 0;border:1px solid var(--ahh-border);border-left:4px solid var(--ahh-accent);border-radius:var(--ahh-radius-md);background:linear-gradient(105deg,#FFF,var(--ahh-surface));box-shadow:var(--ahh-shadow-md)}
    .integration-name{color:var(--ahh-text-primary);font-family:var(--ahh-display);font-weight:760;font-size:1.1rem}.integration-sub{color:var(--ahh-text-muted);font-size:.78rem;margin-top:.18rem}.status-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.55rem .9rem}.status-cell{border-left:1px solid var(--ahh-border);padding-left:.75rem}.status-cell span{display:block;color:var(--ahh-text-muted);font-size:.62rem;text-transform:uppercase;letter-spacing:.08em;font-weight:760}.status-cell strong{display:block;color:var(--ahh-text-primary);font-size:.82rem;margin-top:.15rem;overflow-wrap:anywhere}
    .system-story{display:grid;grid-template-columns:minmax(110px,1fr) auto minmax(110px,1fr) auto minmax(110px,1fr) auto minmax(110px,1fr) auto minmax(110px,1fr);align-items:stretch;gap:.45rem;margin:.75rem 0 1.4rem}.system-story>div{display:flex;flex-direction:column;min-height:126px;padding:.9rem;border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.8);box-shadow:var(--ahh-shadow-sm)}.system-story>div strong{color:var(--ahh-accent);font-size:.64rem;letter-spacing:.1em}.system-story>div span{color:var(--ahh-text-primary);font-family:var(--ahh-display);font-size:.86rem;font-weight:720;margin-top:.5rem}.system-story>div small{color:var(--ahh-text-muted);font-size:.69rem;line-height:1.4;margin-top:.32rem}.system-story>b{align-self:center;color:var(--ahh-accent);font-size:1rem;font-weight:500}
    .decision-hero{display:grid;grid-template-columns:1fr 2fr;gap:.85rem;padding:1rem 1.1rem;margin:.65rem 0;border:1px solid var(--ahh-border);border-left:4px solid var(--ahh-accent);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.84);box-shadow:var(--ahh-shadow-sm)}.decision-hero.decision-eligible{border-left-color:var(--ahh-success);background:linear-gradient(100deg,var(--ahh-success-bg),#FFF 48%)}.decision-hero.decision-rejected{border-left-color:var(--ahh-warning);background:linear-gradient(100deg,var(--ahh-warning-bg),#FFF 48%)}.decision-hero span,.decision-card>span{display:block;color:var(--ahh-text-muted);font-size:.62rem;font-weight:800;letter-spacing:.11em;text-transform:uppercase}.decision-hero strong{display:block;color:var(--ahh-text-primary);font-family:var(--ahh-display);font-size:1.08rem;margin-top:.3rem}.decision-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem;margin:.75rem 0 1rem}.decision-card{min-height:166px;padding:1rem;border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.8);box-shadow:var(--ahh-shadow-sm)}.decision-card strong{display:block;color:var(--ahh-text-primary);font-family:var(--ahh-display);font-size:.94rem;line-height:1.4;margin-top:.55rem}.decision-card p{color:var(--ahh-text-secondary);font-size:.8rem;line-height:1.45;margin:.45rem 0}.decision-card small{color:var(--ahh-text-muted);font-size:.7rem;line-height:1.5}
    .evidence-section{margin:1rem 0;padding:1rem;border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);background:rgba(255,255,255,.72);box-shadow:var(--ahh-shadow-sm)}.evidence-title{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;padding-bottom:.75rem;margin-bottom:.8rem;border-bottom:1px solid var(--ahh-border)}.evidence-title>span{color:var(--ahh-accent-strong);font-size:.68rem;font-weight:850;letter-spacing:.12em;text-transform:uppercase}.evidence-title p{max-width:68%;margin:0;color:var(--ahh-text-muted);font-size:.72rem;line-height:1.45;text-align:right}.evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem}.evidence-fact{min-height:76px;padding:.72rem .8rem;border:1px solid rgba(175,164,145,.45);border-radius:var(--ahh-radius-sm);background:var(--ahh-surface)}.evidence-fact span{display:block;color:var(--ahh-text-muted);font-size:.6rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.evidence-fact strong{display:block;margin-top:.32rem;color:var(--ahh-text-primary);font-size:.8rem;line-height:1.42;font-weight:700}

    /* Streamlit and BaseWeb internals remain light in OS dark mode. */
    [data-testid="stForm"],[data-testid="stExpander"]{border:1px solid var(--ahh-border)!important;border-radius:var(--ahh-radius-md)!important;background:rgba(255,255,255,.67)!important;box-shadow:var(--ahh-shadow-sm);padding:.35rem .6rem}
    [data-testid="stExpander"] summary,[data-testid="stExpander"] summary *{color:var(--ahh-text-primary)!important}
    label,label p,[data-testid="stWidgetLabel"] p{color:var(--ahh-text-primary)!important;font-weight:570}
    input,textarea,select{color:var(--ahh-text-primary)!important;-webkit-text-fill-color:var(--ahh-text-primary)!important;caret-color:var(--ahh-text-primary)!important}
    input::placeholder,textarea::placeholder{color:var(--ahh-text-muted)!important;opacity:1!important}
    [data-baseweb="input"]>div,[data-baseweb="select"]>div,[data-baseweb="textarea"]>div,[data-testid="stNumberInput"]>div>div{color:var(--ahh-text-primary)!important;background:var(--ahh-surface)!important;border-color:var(--ahh-border-strong)!important;border-radius:var(--ahh-radius-sm)!important}
    [data-baseweb="input"] *,[data-baseweb="select"] *,[data-baseweb="textarea"] *{color:var(--ahh-text-primary)!important;-webkit-text-fill-color:var(--ahh-text-primary)!important}
    [data-baseweb="popover"],[data-baseweb="menu"],[role="listbox"],[role="option"]{color:var(--ahh-text-primary)!important;background:var(--ahh-surface-elevated)!important}
    [role="option"]:hover,[role="option"][aria-selected="true"]{background:var(--ahh-accent-bg)!important}
    [data-baseweb="input"]>div:focus-within,[data-baseweb="select"]>div:focus-within,[data-baseweb="textarea"]>div:focus-within{border-color:var(--ahh-accent)!important;box-shadow:0 0 0 3px var(--ahh-focus)!important}
    [data-testid="stToggle"] label,[data-testid="stCheckbox"] label{color:var(--ahh-text-primary)!important}[role="switch"]{outline-color:var(--ahh-accent)!important}[role="switch"][aria-checked="true"]{background:var(--ahh-accent)!important}
    .stButton>button,.stFormSubmitButton>button,.stLinkButton>a{min-height:2.45rem;border-radius:var(--ahh-radius-sm)!important;border:1px solid var(--ahh-border-strong)!important;color:var(--ahh-text-primary)!important;background:var(--ahh-surface-elevated)!important;font-weight:680!important;transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease}
    .stButton>button:hover,.stFormSubmitButton>button:hover,.stLinkButton>a:hover{border-color:var(--ahh-accent)!important;transform:translateY(-1px);box-shadow:var(--ahh-shadow-sm)}
    button[kind="primary"],[data-testid*="stBaseButton-primary"]{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:var(--ahh-hero)!important;border-color:var(--ahh-hero)!important;box-shadow:0 5px 14px rgba(11,32,48,.13)!important}
    button[kind="primary"]:hover,[data-testid*="stBaseButton-primary"]:hover{color:#FFF!important;background:#143347!important;border-color:#143347!important}
    [data-testid="stSegmentedControl"]{margin:.35rem 0 .65rem;overflow-x:auto;padding-bottom:.15rem}[data-testid="stSegmentedControl"]>div{min-width:max-content}
    [data-testid="stSegmentedControl"] button{color:var(--ahh-text-primary)!important;-webkit-text-fill-color:var(--ahh-text-primary)!important;background:var(--ahh-surface-elevated)!important;border-color:var(--ahh-border)!important}
    [data-testid="stSegmentedControl"] button[aria-pressed="true"]{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:var(--ahh-hero)!important;border-color:var(--ahh-hero)!important}
    [data-testid="stSegmentedControl"] button[data-testid="stBaseButton-segmented_controlActive"]{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:var(--ahh-hero)!important;border-color:var(--ahh-hero)!important}
    [data-testid="stTabs"] [role="tablist"]{gap:.35rem;border-bottom:1px solid var(--ahh-border)}[data-testid="stTabs"] [role="tab"]{color:var(--ahh-text-secondary)!important;background:transparent!important;border-radius:8px 8px 0 0;padding:.5rem .7rem!important}[data-testid="stTabs"] [role="tab"][aria-selected="true"]{color:var(--ahh-text-primary)!important;background:var(--ahh-accent-bg)!important;font-weight:720;border-bottom-color:var(--ahh-accent)!important}
    [data-testid="stDataFrame"]{border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);overflow:hidden;background:var(--ahh-surface);box-shadow:var(--ahh-shadow-sm)}[data-testid="stDataFrame"] *{font-family:var(--ahh-font)!important}
    [data-testid="stTable"]{overflow-x:auto;border:1px solid var(--ahh-border);border-radius:var(--ahh-radius-md);background:var(--ahh-surface)}[data-testid="stTable"] th{color:var(--ahh-text-inverse)!important;background:var(--ahh-hero)!important}[data-testid="stTable"] td{color:var(--ahh-text-primary)!important;background:var(--ahh-surface)!important}
    [data-testid="stAlert"]{border-radius:var(--ahh-radius-md);border:1px solid var(--ahh-border);box-shadow:var(--ahh-shadow-sm)}[data-testid="stAlert"] p{color:var(--ahh-text-primary)!important}
    [data-testid="stNotification"],[data-testid="stCode"],[data-testid="stJson"]{border-radius:var(--ahh-radius-md)}[data-testid="stJson"]{border:1px solid var(--ahh-border);background:var(--ahh-surface)!important}
    [data-testid="stCaptionContainer"]{color:var(--ahh-text-muted)!important}[data-testid="stBaseButton-headerNoPadding"]{color:#C8D2D8!important;background:rgba(255,255,255,.06)!important}.readonly{color:var(--ahh-warning);font-weight:720}

    /* This intentionally follows the generic button rules in the cascade. */
    [data-testid="stSidebar"] .stButton>button{color:#E8EDF0!important;-webkit-text-fill-color:#E8EDF0!important;background:transparent!important;border-color:transparent!important;box-shadow:none!important}
    [data-testid="stSidebar"] .stButton>button:hover{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:rgba(255,255,255,.065)!important;border-color:rgba(231,214,184,.2)!important}
    [data-testid="stSidebar"] .stButton>button[kind="primary"],[data-testid="stSidebar"] .stButton>[data-testid*="stBaseButton-primary"]{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:linear-gradient(90deg,rgba(173,129,66,.23),rgba(255,255,255,.065))!important;border-color:rgba(231,214,184,.38)!important;box-shadow:inset 3px 0 0 var(--ahh-accent)!important}
    [data-testid="stSidebar"] [data-testid="stButton"]>button[data-testid="stBaseButton-secondary"]{color:#E8EDF0!important;-webkit-text-fill-color:#E8EDF0!important;background:transparent!important;border-color:transparent!important;box-shadow:none!important}
    [data-testid="stSidebar"] [data-testid="stButton"]>button[data-testid="stBaseButton-secondary"]:hover{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:rgba(255,255,255,.065)!important;border-color:rgba(231,214,184,.2)!important}
    [data-testid="stSidebar"] [data-testid="stButton"]>button[data-testid="stBaseButton-primary"]{color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:linear-gradient(90deg,rgba(173,129,66,.23),rgba(255,255,255,.065))!important;border-color:rgba(231,214,184,.38)!important;box-shadow:inset 3px 0 0 var(--ahh-accent)!important}
    .stFormSubmitButton>button[data-testid="stBaseButton-primaryFormSubmit"]:not(:disabled){color:#FFF!important;-webkit-text-fill-color:#FFF!important;background:var(--ahh-hero)!important;border-color:var(--ahh-hero)!important;box-shadow:0 5px 14px rgba(11,32,48,.13)!important}

    @media(max-width:1100px){.block-container{padding:3.1rem 1.25rem 4.5rem}[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{flex:1 1 15rem!important;width:auto!important;min-width:min(100%,15rem)!important}.snapshot-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.adapter-truth-panel{grid-template-columns:1fr}.system-story{grid-template-columns:repeat(2,minmax(0,1fr))}.system-story>b{display:none}}
    @media(max-width:768px){[data-testid="stAppViewContainer"],[data-testid="stMain"]{margin-left:0!important;width:100%!important;max-width:100%!important}.block-container{width:100%!important;max-width:100%!important;margin-left:0!important;padding:3.2rem .8rem 3.5rem}.hero{margin-top:.15rem;padding:1.35rem 1.05rem;border-radius:17px}.brand-row{margin-bottom:.9rem}.hero-copy{font-size:1rem}.hero-subcopy{font-size:.8rem}.hero-meta{gap:.35rem;margin-top:1rem}.hero-chip{font-size:.65rem;padding:.3rem .55rem}.masthead{align-items:flex-start;flex-direction:column}[data-testid="stHorizontalBlock"]{flex-direction:column!important;gap:.72rem!important}[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{flex:1 1 auto!important;width:100%!important;min-width:100%!important}[data-testid="stMetric"]{min-height:96px}.snapshot-grid,.status-grid,.decision-grid,.decision-hero,.evidence-grid{grid-template-columns:1fr}.evidence-title{display:block}.evidence-title p{max-width:none;margin-top:.4rem;text-align:left}.status-cell{border-left:0;border-top:1px solid var(--ahh-border);padding:.55rem 0 0}.page-intro{margin-top:.55rem}.system-story{grid-template-columns:1fr;overflow:visible}.system-story>div{min-height:104px}}
    @media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;scroll-behavior:auto!important}}
    
    /* AADIL_EXECUTIVE_UI_V2_GLOBAL */
    :root{
      --munshi-v2-ink:#102C3F;
      --munshi-v2-muted:#697A8A;
      --munshi-v2-green:#123F34;
      --munshi-v2-green-2:#1C5748;
      --munshi-v2-line:rgba(28,55,72,.105);
      --munshi-v2-bg:#F7F4EC;
      --munshi-v2-surface:rgba(255,255,255,.86);
      --munshi-v2-shadow:0 10px 30px rgba(25,48,62,.065);
      --munshi-v2-shadow-lg:0 18px 50px rgba(25,48,62,.09);
      --munshi-v2-radius:22px;
      --munshi-v2-radius-sm:14px;
    }

    html,body,.stApp,[data-baseweb]{
      color-scheme:light!important;
    }

    .stApp{
      background:
        radial-gradient(circle at 8% 0%,rgba(255,230,175,.28),transparent 28rem),
        radial-gradient(circle at 92% 7%,rgba(213,244,231,.30),transparent 30rem),
        radial-gradient(circle at 62% 55%,rgba(230,224,255,.18),transparent 34rem),
        linear-gradient(180deg,#FAF8F2 0%,#F6F3EA 58%,#F3F0E8 100%)!important;
      color:var(--munshi-v2-ink)!important;
    }

    [data-testid="stHeader"]{
      background:rgba(250,248,242,.80)!important;
      backdrop-filter:blur(18px)!important;
      border-bottom:1px solid rgba(28,55,72,.055)!important;
      box-shadow:0 3px 18px rgba(25,48,62,.025)!important;
    }

    .block-container{
      max-width:1580px!important;
      padding:2.55rem 2rem 5rem!important;
    }

    h1,h2,h3,h4,h5,h6{
      color:var(--munshi-v2-ink)!important;
      letter-spacing:-.025em!important;
    }

    h1{
      font-size:clamp(2rem,2.8vw,2.85rem)!important;
      font-weight:790!important;
      line-height:1.04!important;
    }

    h2{font-weight:760!important}
    h3,h4{font-weight:720!important}

    p,li,label,[data-testid="stCaptionContainer"]{
      line-height:1.55!important;
    }

    [data-testid="stCaptionContainer"]{
      color:#788797!important;
    }

    /* Premium light masthead: same soft visual language as Job Rankings. */
    .hero{
      color:var(--munshi-v2-ink)!important;
      background:
        radial-gradient(circle at 92% 15%,rgba(210,244,229,.68),transparent 25rem),
        radial-gradient(circle at 10% 110%,rgba(255,232,177,.48),transparent 22rem),
        linear-gradient(135deg,#FFFDF5 0%,#F7FAF7 48%,#F5F1FF 100%)!important;
      border:1px solid var(--munshi-v2-line)!important;
      border-radius:28px!important;
      box-shadow:var(--munshi-v2-shadow-lg)!important;
      padding:clamp(1.55rem,2.8vw,2.45rem)!important;
    }
    .hero *{color:inherit!important}
    .hero p{color:#657789!important}
    .hero-chip{
      color:#284A40!important;
      background:rgba(255,255,255,.72)!important;
      border:1px solid rgba(24,63,52,.10)!important;
      box-shadow:0 2px 8px rgba(25,48,62,.04)!important;
      border-radius:999px!important;
    }

    /* Native Streamlit KPI cards. */
    div[data-testid="stMetric"]{
      background:linear-gradient(145deg,rgba(255,255,255,.96),rgba(255,255,255,.78))!important;
      border:1px solid var(--munshi-v2-line)!important;
      border-radius:var(--munshi-v2-radius)!important;
      box-shadow:var(--munshi-v2-shadow)!important;
      padding:1.05rem 1.15rem!important;
      min-height:7rem!important;
      overflow:hidden!important;
    }
    div[data-testid="stMetric"] label,
    div[data-testid="stMetric"] [data-testid="stMetricLabel"]{
      color:#66798A!important;
      font-weight:700!important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"]{
      color:var(--munshi-v2-ink)!important;
      font-weight:800!important;
      letter-spacing:-.035em!important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5n+1) div[data-testid="stMetric"]{
      background:linear-gradient(145deg,#FFF5C9 0%,#FFFDF4 76%,#FFFFFF 100%)!important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5n+2) div[data-testid="stMetric"]{
      background:linear-gradient(145deg,#EEE7FF 0%,#FAF8FF 76%,#FFFFFF 100%)!important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5n+3) div[data-testid="stMetric"]{
      background:linear-gradient(145deg,#DDF7EA 0%,#F6FCF8 76%,#FFFFFF 100%)!important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5n+4) div[data-testid="stMetric"]{
      background:linear-gradient(145deg,#FFE8C8 0%,#FFF9F1 76%,#FFFFFF 100%)!important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5n+5) div[data-testid="stMetric"]{
      background:linear-gradient(145deg,#E9E8FF 0%,#FAF9FF 76%,#FFFFFF 100%)!important;
    }

    /* Forms, expanders and surfaced workspaces. */
    [data-testid="stForm"],
    [data-testid="stExpander"]{
      background:rgba(255,255,255,.82)!important;
      border:1px solid var(--munshi-v2-line)!important;
      border-radius:var(--munshi-v2-radius)!important;
      box-shadow:var(--munshi-v2-shadow)!important;
      padding:.55rem .75rem!important;
    }
    [data-testid="stExpander"] summary{
      min-height:3.1rem!important;
      border-radius:15px!important;
    }
    [data-testid="stExpander"] summary:hover{
      background:rgba(18,63,52,.035)!important;
    }

    /* Inputs feel like consumer product controls, not admin widgets. */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input{
      background:rgba(255,255,255,.94)!important;
      color:var(--munshi-v2-ink)!important;
      border:1px solid rgba(32,62,80,.13)!important;
      border-radius:var(--munshi-v2-radius-sm)!important;
      box-shadow:0 2px 8px rgba(25,48,62,.035)!important;
    }
    [data-testid="stTextInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus,
    [data-testid="stNumberInput"] input:focus,
    [data-testid="stDateInput"] input:focus{
      border-color:rgba(18,63,52,.36)!important;
      box-shadow:0 0 0 3px rgba(18,63,52,.07)!important;
    }

    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div{
      background:rgba(255,255,255,.94)!important;
      color:var(--munshi-v2-ink)!important;
      border:1px solid rgba(32,62,80,.13)!important;
      border-radius:var(--munshi-v2-radius-sm)!important;
      box-shadow:0 2px 8px rgba(25,48,62,.035)!important;
      min-height:2.75rem!important;
    }

    [data-testid="stCheckbox"],
    [data-testid="stToggle"]{
      padding:.2rem 0!important;
    }

    /* Buttons. Keep sidebar rules separate below. */
    [data-testid="stMain"] [data-testid="stButton"] > button,
    [data-testid="stMain"] [data-testid="stDownloadButton"] > button,
    [data-testid="stMain"] a[data-testid*="stBaseButton"]{
      border-radius:999px!important;
      min-height:2.65rem!important;
      font-weight:680!important;
      border:1px solid rgba(35,62,78,.18)!important;
      background:rgba(255,255,255,.90)!important;
      color:#294456!important;
      box-shadow:0 4px 14px rgba(25,48,62,.045)!important;
      transition:transform .14s ease,box-shadow .14s ease,border-color .14s ease!important;
    }
    [data-testid="stMain"] [data-testid="stButton"] > button:hover,
    [data-testid="stMain"] [data-testid="stDownloadButton"] > button:hover,
    [data-testid="stMain"] a[data-testid*="stBaseButton"]:hover{
      transform:translateY(-1px)!important;
      border-color:rgba(18,63,52,.30)!important;
      box-shadow:0 8px 20px rgba(25,48,62,.075)!important;
    }
    [data-testid="stMain"] [data-testid="stButton"] > button[kind="primary"],
    [data-testid="stMain"] [data-testid="stButton"] > button[data-testid*="primary"],
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] > button{
      color:#FFF!important;
      -webkit-text-fill-color:#FFF!important;
      background:linear-gradient(135deg,#0F382F,#185246)!important;
      border-color:#123F34!important;
      box-shadow:0 7px 20px rgba(18,63,52,.17)!important;
    }
    [data-testid="stMain"] button:disabled{
      opacity:.55!important;
      box-shadow:none!important;
      transform:none!important;
    }

    /* Tabs become a compact segmented navigation. */
    [data-testid="stTabs"] [role="tablist"]{
      display:inline-flex!important;
      gap:.28rem!important;
      padding:.32rem!important;
      margin-bottom:.8rem!important;
      background:rgba(255,255,255,.78)!important;
      border:1px solid var(--munshi-v2-line)!important;
      border-radius:17px!important;
      box-shadow:0 4px 15px rgba(25,48,62,.04)!important;
    }
    [data-testid="stTabs"] [role="tab"]{
      border:0!important;
      border-radius:13px!important;
      padding:.55rem .85rem!important;
      color:#637587!important;
      font-weight:650!important;
    }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"]{
      color:#FFF!important;
      background:linear-gradient(135deg,#123F34,#1C5748)!important;
      box-shadow:0 4px 12px rgba(18,63,52,.15)!important;
    }

    /* Tables/DataFrames get the same soft-shell treatment. */
    [data-testid="stDataFrame"],
    [data-testid="stTable"]{
      border:1px solid var(--munshi-v2-line)!important;
      border-radius:22px!important;
      background:rgba(255,255,255,.86)!important;
      box-shadow:var(--munshi-v2-shadow)!important;
      overflow:hidden!important;
    }

    /* Charts become surfaced modules. */
    [data-testid="stVegaLiteChart"],
    [data-testid="stArrowVegaLiteChart"],
    [data-testid="stPlotlyChart"]{
      background:rgba(255,255,255,.78)!important;
      border:1px solid var(--munshi-v2-line)!important;
      border-radius:22px!important;
      box-shadow:var(--munshi-v2-shadow)!important;
      padding:.55rem!important;
      overflow:hidden!important;
    }

    /* Alerts are lighter and more product-like. */
    [data-testid="stAlert"]{
      border-radius:18px!important;
      border:1px solid rgba(32,62,80,.105)!important;
      box-shadow:0 5px 18px rgba(25,48,62,.045)!important;
      backdrop-filter:blur(8px)!important;
    }

    [data-testid="stJson"],
    [data-testid="stCode"]{
      border-radius:18px!important;
      border:1px solid var(--munshi-v2-line)!important;
      box-shadow:0 5px 18px rgba(25,48,62,.04)!important;
    }

    /* Sidebar: premium forest-green navigation, still preserving custom nav only. */
    [data-testid="stSidebar"]{
      background:
        radial-gradient(circle at 20% -5%,rgba(243,202,125,.16),transparent 22rem),
        linear-gradient(180deg,#0B2C25 0%,#0A251F 55%,#081F1A 100%)!important;
      border-right:1px solid rgba(255,255,255,.08)!important;
      box-shadow:14px 0 38px rgba(9,31,26,.11)!important;
    }
    [data-testid="stSidebar"] > div{
      padding-top:1.15rem!important;
    }
    [data-testid="stSidebar"] button{
      border-radius:13px!important;
      min-height:2.55rem!important;
      padding:.52rem .78rem!important;
      font-weight:610!important;
      transition:transform .13s ease,background .13s ease,border-color .13s ease!important;
    }
    [data-testid="stSidebar"] button:hover{
      transform:translateX(2px)!important;
      background:rgba(255,255,255,.075)!important;
      border-color:rgba(255,255,255,.12)!important;
    }
    [data-testid="stSidebar"] button[kind="primary"],
    [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"]{
      color:#FFF!important;
      -webkit-text-fill-color:#FFF!important;
      background:linear-gradient(135deg,rgba(49,116,94,.72),rgba(255,255,255,.07))!important;
      border:1px solid rgba(230,241,236,.17)!important;
      box-shadow:inset 3px 0 0 #F0CB83,0 5px 16px rgba(0,0,0,.09)!important;
    }
    .sidebar-mark{
      width:2.55rem!important;
      height:2.55rem!important;
      border-radius:16px!important;
      border:1px solid rgba(240,203,131,.42)!important;
      background:linear-gradient(145deg,rgba(240,203,131,.18),rgba(255,255,255,.055))!important;
      color:#F3D79D!important;
      box-shadow:0 5px 16px rgba(0,0,0,.09)!important;
    }
    .sidebar-title{font-size:1.02rem!important;letter-spacing:.015em!important}
    .nav-group{
      color:#A9BBB4!important;
      margin-top:1.15rem!important;
      letter-spacing:.16em!important;
    }

    /* Real dialogs align with the Job Rankings modal. */
    [data-testid="stDialog"]{
      backdrop-filter:blur(6px)!important;
    }
    [data-testid="stDialog"] [role="dialog"]{
      border-radius:26px!important;
      border:1px solid rgba(255,255,255,.52)!important;
      box-shadow:0 30px 85px rgba(11,40,33,.24)!important;
    }

    /* Keep the Job Rankings cards as the design reference; don't flatten them. */
    .munshi-card-v16{
      box-shadow:0 10px 30px rgba(25,48,62,.07)!important;
    }

    @media (max-width:900px){
      .block-container{padding:2rem 1rem 4rem!important}
      div[data-testid="stMetric"]{min-height:6.25rem!important}
    }

    
    /* AADIL_EXECUTIVE_UI_V2_N8N_HERO_FIX_V1_7 */
    .hero h1{
      color:#102C3F!important;
      -webkit-text-fill-color:#102C3F!important;
      text-shadow:none!important;
    }
    .hero .eyebrow{
      color:#9A6A29!important;
      -webkit-text-fill-color:#9A6A29!important;
    }

    </style>
    """
