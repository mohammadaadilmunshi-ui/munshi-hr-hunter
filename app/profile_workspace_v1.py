"""First-class MUNSHI Profile workspace.

The confirmed Native Resume profile becomes the permanent read surface for the
candidate. Resume Studio remains the source/generation workspace. This page never
re-infers candidate truth, never mutates a confirmed extraction while rendering,
and never turns browser/profile state into submission evidence.
"""
from __future__ import annotations

import html
import re
from typing import Any

import streamlit as st

from app import native_resume_service_v3 as v3
from app.product_state import master_resume, tracker_rows
from app.product_ui import esc, safe_link
from app.profile_brand_resolver import resolve_brand_assets
from app.resume_profile_details_v31 import load_candidate_profile_details

PROFILE_TABS = {
    "Resume": "resume",
    "Cover Letter": "cover-letter",
    "Profile Details": "details",
}


def _inject_css() -> None:
    st.markdown(
        """
<style>
.profile-workspace{margin-top:-.35rem}
.profile-subnav{display:flex;align-items:center;gap:.55rem;padding:.52rem .15rem .9rem;border-bottom:1px solid var(--m-border);margin-bottom:1.15rem;overflow-x:auto;white-space:nowrap}
.profile-selector{display:inline-flex;align-items:center;justify-content:space-between;gap:1rem;min-width:250px;height:46px;padding:0 .95rem;border:1px solid var(--m-strong);border-radius:11px;background:#fff;color:var(--m-ink);font-weight:660;box-shadow:0 2px 7px rgba(20,33,41,.05)}
.profile-subaction,.profile-tab-link{display:inline-flex;align-items:center;justify-content:center;height:42px;padding:0 .75rem;border-radius:11px;text-decoration:none!important;color:var(--m-secondary)!important;font-weight:650;font-size:.88rem}
.profile-subaction:hover,.profile-tab-link:hover{background:var(--m-soft);color:var(--m-forest)!important}.profile-tab-link.active{background:var(--m-active);color:var(--m-forest)!important;border:1px solid #cfddd5}
.profile-disabled{opacity:.43;cursor:not-allowed}
.profile-hero{display:flex;align-items:center;justify-content:space-between;gap:1.1rem;background:#fff;border:1px solid var(--m-border);border-radius:22px;padding:1.25rem 1.35rem;margin-bottom:1.3rem;box-shadow:0 5px 18px rgba(20,33,41,.035)}
.profile-identity{display:flex;align-items:center;gap:1rem;min-width:0}.profile-avatar{width:64px;height:64px;border-radius:18px;background:var(--m-forest);display:grid;place-items:center;color:#fff;font-weight:800;font-size:1.12rem;flex:none}.profile-name-row{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}.profile-name{font-size:1.14rem;font-weight:780;color:var(--m-forest)}.profile-contact{color:var(--m-muted);font-size:.84rem;margin-top:.22rem;overflow-wrap:anywhere}.profile-badge{display:inline-flex;align-items:center;border-radius:999px;padding:.28rem .55rem;font-size:.7rem;font-weight:720;background:#e8f7ee;color:#087555}.profile-complete{background:#fff8df;color:#a65d00;border:1px solid #f2d370}
.profile-edit-link{color:var(--m-forest)!important;text-decoration:none!important;font-size:.82rem;font-weight:720;white-space:nowrap}
.profile-section-label{display:flex;align-items:center;gap:.7rem;margin:.2rem 0 .95rem}.profile-section-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:14px;font-size:1.05rem;background:#eef7f1}.profile-section-icon.edu{background:#f2edff;color:#7142e8}.profile-section-icon.exp{background:#e8f8ef;color:#0f8a61}.profile-section-icon.proj{background:#fff4e5;color:#d46800}.profile-section-icon.skill{background:#edf5ff;color:#2d67c9}.profile-section-icon.cert{background:#fff7df;color:#bd6300}.profile-section-icon.defaults{background:#e9fbff;color:#07849d}.profile-section-title{font-size:1.08rem;font-weight:760;color:var(--m-ink)}.profile-section-count{font-size:.8rem;color:var(--m-muted);margin-top:.05rem}.profile-section-edit{margin-left:auto;color:#80909a!important;text-decoration:none!important;font-size:1rem}
.profile-left-card,.profile-right-card{background:#fff;border:1px solid var(--m-border);border-radius:22px;padding:1.35rem;margin-bottom:1rem}.profile-summary{font-size:.91rem;line-height:1.62;color:#59666e;margin-bottom:1.65rem}
.profile-entry{display:flex;gap:.85rem;margin:.75rem 0 1.05rem}.profile-logo{width:48px;height:48px;border-radius:13px;border:1px solid #e2e8e4;background:#fff;display:grid;place-items:center;overflow:hidden;flex:none}.profile-logo img{width:100%;height:100%;object-fit:contain;padding:5px}.profile-logo-fallback{width:100%;height:100%;display:grid;place-items:center;background:#eef3f0;color:var(--m-forest);font-size:.72rem;font-weight:800}.profile-entry-title{font-size:.95rem;font-weight:720;color:var(--m-ink);line-height:1.25}.profile-entry-subtitle{font-size:.84rem;color:#52616b;margin-top:.15rem}.profile-entry-meta{font-size:.75rem;color:#7a878f;margin-top:.18rem}.profile-entry-details{font-size:.78rem;color:#64727a;margin-top:.35rem}.profile-entry-details li{margin:.2rem 0}
.profile-timeline{position:relative;margin:.2rem 0 1.4rem;padding-left:1rem}.profile-timeline:before{content:"";position:absolute;left:9px;top:18px;bottom:12px;width:1px;background:#dce6df}.profile-role{position:relative;display:flex;gap:.85rem;padding:0 0 1.25rem 1.3rem}.profile-role:before{content:"";position:absolute;left:-.1rem;top:20px;width:10px;height:10px;border-radius:50%;background:#123d31;border:2px solid #fff;box-shadow:0 0 0 1px #cddad2}.profile-role:nth-child(even):before{background:#cbd8d0}.profile-bullets{margin:.45rem 0 0;padding-left:1rem;color:#68757d;font-size:.78rem;line-height:1.48}.profile-bullets li{margin:.22rem 0}.profile-project{margin:0 0 1.2rem}.profile-project-title{font-size:.94rem;font-weight:730;color:var(--m-ink)}
.profile-chips{display:flex;flex-wrap:wrap;gap:.42rem;margin:.45rem 0}.profile-chip{display:inline-flex;align-items:center;border-radius:999px;padding:.35rem .62rem;background:#e8faf1;color:#087252;font-size:.72rem;font-weight:680}.profile-chip.orange{background:#fff3e5;color:#b85d12}.profile-chip.gray{background:#f0f2f4;color:#75808a}.profile-chip.yes:before{content:"✓";margin-right:.3rem}.profile-chip.no:before{content:"×";margin-right:.3rem}.profile-chip.unknown:before{content:"•";margin-right:.3rem}
.profile-default-box{border:1px solid #e6ebe8;border-radius:15px;padding:.9rem;margin:.45rem 0 1rem}.profile-default-row{display:flex;justify-content:space-between;gap:1rem;font-size:.78rem;color:#7a858d;margin:.24rem 0}.profile-default-row strong{color:#283640;font-weight:680;text-align:right}.profile-mini-heading{font-size:.68rem;letter-spacing:.07em;color:#697680;font-weight:800;margin:1rem 0 .42rem}.profile-skill-group{display:grid;grid-template-columns:minmax(115px,.42fr) 1fr;gap:.7rem;align-items:start;margin:.8rem 0}.profile-skill-label{font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;color:#687680;font-weight:790;padding-top:.38rem}
.profile-cert-card{display:flex;align-items:center;gap:.8rem;border:1px solid #e6ebe8;border-radius:16px;padding:.72rem;margin:.52rem 0}.profile-cert-card .profile-logo{width:48px;height:48px}.profile-cert-name{font-size:.84rem;font-weight:720;color:var(--m-ink)}.profile-cert-issuer{font-size:.76rem;color:#65737d;margin-top:.12rem}
.profile-empty{border:1px dashed var(--m-strong);border-radius:20px;background:#fff;padding:2rem;text-align:center}.profile-empty h3{margin-top:0!important}.profile-brand-note{font-size:.68rem;color:#8a959b;margin:.9rem 0 0}
@media(max-width:900px){.profile-subnav{padding-bottom:.75rem}.profile-selector{min-width:190px}.profile-hero{align-items:flex-start}.profile-contact{font-size:.76rem}.profile-skill-group{grid-template-columns:1fr}.profile-skill-label{padding-top:0}}
</style>
""",
        unsafe_allow_html=True,
    )


def _tab_from_state() -> str:
    value = str(st.session_state.get("product_profile_tab") or "Profile details")
    if value == "Cover letters":
        value = "Cover Letter"
    elif value == "Profile details":
        value = "Profile Details"
    elif value not in PROFILE_TABS:
        value = "Profile Details"
    st.session_state["product_profile_tab"] = {
        "Cover Letter": "Cover letters",
        "Profile Details": "Profile details",
        "Resume": "Resume",
    }[value]
    return value


def _subnav(tab: str) -> None:
    links = []
    for label, route in PROFILE_TABS.items():
        active = " active" if label == tab else ""
        links.append(
            f'<a class="profile-tab-link{active}" href="?view=profile&amp;tab={route}" target="_self">{html.escape(label)}</a>'
        )
    st.markdown(
        """<div class="profile-workspace"><div class="profile-subnav">
        <div class="profile-selector"><span>Default</span><span>⌄</span></div>
        <span class="profile-subaction profile-disabled" title="Multiple profile lanes will be enabled only after independent profile-source authority is added">☆</span>
        <span class="profile-subaction profile-disabled" title="The Default truth profile cannot be deleted">⌫</span>
        <span class="profile-subaction profile-disabled" title="Multiple profile lanes are reserved by the architecture but not yet authoritative">＋ Add Profile</span>
        %s
        </div></div>""" % "".join(links),
        unsafe_allow_html=True,
    )


def _latest_confirmed_profile() -> dict[str, Any]:
    connection = v3.v2.v1.get_connection()
    extraction_id = ""
    try:
        v3.ensure_schema(connection)
        owner = v3.v2.v1.current_owner(connection)
        row = connection.execute(
            """SELECT extraction_id FROM native_resume_profile_extracts
               WHERE tenant_id=? AND user_id=? AND status='CONFIRMED'
               ORDER BY COALESCE(confirmed_at,created_at) DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        if row:
            extraction_id = str(row["extraction_id"])
    finally:
        connection.close()
    return v3.get_profile_extract(extraction_id) if extraction_id else {}


def _initials(name: str) -> str:
    parts = [part for part in re.split(r"\s+", str(name or "").strip()) if part]
    if not parts:
        return "M"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _completion(profile: dict[str, Any], details: dict[str, Any]) -> int:
    contact = profile.get("contact") or {}
    checks = [
        contact.get("full_name"), contact.get("location"), contact.get("email"),
        profile.get("professional_summary"), bool(profile.get("education")),
        bool(profile.get("experience")), bool(profile.get("projects")), bool(profile.get("skills")),
        bool(profile.get("certifications")), details.get("work_authorization_country"),
        details.get("authorized_to_work") is not None, details.get("sponsorship_required") is not None,
        details.get("willing_to_relocate") is not None,
    ]
    return round(100 * sum(bool(value) for value in checks) / len(checks))


def _bool_chip(label: str, value: bool | None) -> str:
    css = "yes" if value is True else "no" if value is False else "gray unknown"
    return f'<span class="profile-chip {css}">{html.escape(label)}</span>'


def _logo_html(asset: dict[str, Any], name: str) -> str:
    logo = str(asset.get("logo_url") or "")
    initials = html.escape(str(asset.get("initials") or _initials(name)))
    if logo.startswith("https://"):
        return (
            '<div class="profile-logo">'
            f'<img src="{html.escape(logo, quote=True)}" alt="{html.escape(name, quote=True)} logo" loading="lazy" '
            'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\'">'
            f'<span class="profile-logo-fallback" style="display:none">{initials}</span></div>'
        )
    return f'<div class="profile-logo"><span class="profile-logo-fallback">{initials}</span></div>'


def _asset_for(assets: dict[tuple[str, str], dict[str, Any]], name: str, kind: str) -> dict[str, Any]:
    wanted = re.sub(r"[^a-z0-9]+", " ", str(name or "").casefold()).strip()
    return assets.get((wanted, kind)) or {"initials": _initials(name), "logo_url": ""}


def _collect_assets(profile: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    requests: list[tuple[str, str]] = []
    for item in profile.get("education") or []:
        if item.get("institution"):
            requests.append((str(item["institution"]), "education"))
    for item in profile.get("experience") or []:
        if item.get("employer"):
            requests.append((str(item["employer"]), "employer"))
    for item in profile.get("certifications") or []:
        if item.get("issuer"):
            requests.append((str(item["issuer"]), "certification"))
    return resolve_brand_assets(requests)


def _hero(profile: dict[str, Any], details: dict[str, Any]) -> None:
    contact = profile.get("contact") or {}
    name = str(contact.get("full_name") or "Candidate")
    contact_line = " · ".join(
        str(value) for value in (contact.get("location"), contact.get("email"), contact.get("phone")) if value
    )
    completion = _completion(profile, details)
    open_to_work = details.get("open_to_work")
    open_badge = '<span class="profile-badge">● Open to work</span>' if open_to_work is not False else ""
    st.markdown(
        f"""<div class="profile-hero">
        <div class="profile-identity"><div class="profile-avatar">{html.escape(_initials(name))}</div><div>
        <div class="profile-name-row"><span class="profile-name">{esc(name)}</span>{open_badge}<span class="profile-badge profile-complete">{completion}% complete →</span></div>
        <div class="profile-contact">{esc(contact_line, 'Contact details not yet confirmed')}</div></div></div>
        <a class="profile-edit-link" href="?view=resume-studio" target="_self">Edit →</a></div>""",
        unsafe_allow_html=True,
    )


def _section_header(icon: str, title: str, count: str, css: str = "", *, edit: bool = True) -> str:
    edit_link = '<a class="profile-section-edit" href="?view=resume-studio" target="_self" title="Edit source in Resume Studio">✎</a>' if edit else ""
    return (
        f'<div class="profile-section-label"><span class="profile-section-icon {css}">{icon}</span>'
        f'<div><div class="profile-section-title">{html.escape(title)}</div><div class="profile-section-count">{html.escape(count)}</div></div>{edit_link}</div>'
    )


def _render_profile_details(profile: dict[str, Any], details: dict[str, Any]) -> None:
    assets = _collect_assets(profile)
    _hero(profile, details)
    left, right = st.columns((1.72, 1), gap="large")
    with left:
        with st.container(key="profile_details_left"):
            chunks: list[str] = ['<div class="profile-left-card">']
            chunks.append('<div style="display:flex;align-items:center;gap:.6rem"><div class="profile-section-title">Professional summary</div><a class="profile-section-edit" href="?view=resume-studio" target="_self">✎</a></div>')
            chunks.append(f'<div class="profile-summary">{esc(profile.get("professional_summary"), "No professional summary was extracted.")}</div>')

            education = profile.get("education") or []
            chunks.append(_section_header("◆", "Education", f"{len(education)} entr{'y' if len(education)==1 else 'ies'}", "edu"))
            for item in education:
                institution = str(item.get("institution") or "Institution")
                asset = _asset_for(assets, institution, "education")
                degree = " · ".join(str(value) for value in (item.get("degree"), item.get("field")) if value)
                dates = " – ".join(str(value) for value in (item.get("start_date"), item.get("end_date")) if value)
                meta = " · ".join(str(value) for value in (dates, item.get("gpa"), item.get("location")) if value)
                details_html = "".join(f"<li>{esc(value)}</li>" for value in item.get("details") or [])
                chunks.append(f'<div class="profile-entry">{_logo_html(asset,institution)}<div><div class="profile-entry-title">{esc(institution)}</div><div class="profile-entry-subtitle">{esc(degree, "Degree details not recorded")}</div><div class="profile-entry-meta">{esc(meta, "")}</div>' + (f'<ul class="profile-entry-details">{details_html}</ul>' if details_html else "") + '</div></div>')

            experience = profile.get("experience") or []
            chunks.append(_section_header("▣", "Experience", f"{len(experience)} role{'s' if len(experience)!=1 else ''}", "exp"))
            chunks.append('<div class="profile-timeline">')
            for item in experience:
                employer = str(item.get("employer") or "Employer")
                asset = _asset_for(assets, employer, "employer")
                dates = " – ".join(str(value) for value in (item.get("start_date"), item.get("end_date")) if value)
                meta = " · ".join(str(value) for value in (dates, item.get("location")) if value)
                bullets = "".join(f"<li>{esc(value)}</li>" for value in item.get("bullets") or [])
                chunks.append(f'<div class="profile-role">{_logo_html(asset,employer)}<div><div class="profile-entry-title">{esc(item.get("title"), "Role")}</div><div class="profile-entry-subtitle">{esc(employer)}</div><div class="profile-entry-meta">{esc(meta, "")}</div>' + (f'<ul class="profile-bullets">{bullets}</ul>' if bullets else "") + '</div></div>')
            chunks.append('</div>')

            projects = profile.get("projects") or []
            chunks.append(_section_header("➤", "Projects", f"{len(projects)} project{'s' if len(projects)!=1 else ''}", "proj"))
            for item in projects:
                tools = "".join(f'<span class="profile-chip orange">{esc(tool)}</span>' for tool in item.get("tools") or [])
                bullets = "".join(f"<li>{esc(value)}</li>" for value in item.get("bullets") or [])
                chunks.append(f'<div class="profile-project"><div class="profile-project-title">{esc(item.get("name"), "Project")}</div><div class="profile-chips">{tools}</div>')
                if item.get("description"):
                    chunks.append(f'<div class="profile-entry-subtitle">{esc(item.get("description"))}</div>')
                if bullets:
                    chunks.append(f'<ul class="profile-bullets">{bullets}</ul>')
                chunks.append('</div>')
            chunks.append('<div class="profile-brand-note">Resume-derived sections remain grounded in the confirmed Master Resume. Organization logos use public Wikidata/Wikimedia metadata and cached fallbacks; private candidate data is not sent to the logo resolver.</div>')
            chunks.append('</div>')
            st.markdown("".join(chunks), unsafe_allow_html=True)

    with right:
        with st.container(key="profile_details_right"):
            chunks = ['<div class="profile-right-card">']
            chunks.append(_section_header("▤", "Application defaults", "What MUNSHI may reuse on ATS forms", "defaults"))
            chunks.append('<div class="profile-mini-heading">WORK AUTHORIZATION</div><div class="profile-default-box">')
            for label, key in (("Country", "work_authorization_country"),("Authorization basis", "authorization_basis"),("Work permit or visa", "visa_or_permit"),("Status", "authorization_status")):
                chunks.append(f'<div class="profile-default-row"><span>{html.escape(label)}</span><strong>{esc(details.get(key), "Not answered")}</strong></div>')
            chunks.append('</div><div class="profile-chips">')
            chunks.append(_bool_chip("Authorized to work", details.get("authorized_to_work")))
            chunks.append(_bool_chip("Needs sponsorship", details.get("sponsorship_required")))
            chunks.append('</div>')
            chunks.append('<div class="profile-mini-heading">WORK PREFERENCES</div><div class="profile-chips">')
            for label, key in (("In-person OK","in_person_ok"),("Can relocate","willing_to_relocate"),("Start immediately","start_immediately"),("Has transport","has_transport"),("Needs accommodations","needs_accommodations")):
                chunks.append(_bool_chip(label, details.get(key)))
            for mode in details.get("work_modes") or []:
                chunks.append(f'<span class="profile-chip">{esc(mode)}</span>')
            chunks.append('</div><div class="profile-mini-heading">BACKGROUND</div><div class="profile-chips">')
            for label, key in (("Prior employee","prior_employee"),("Gov clearance","government_clearance"),("Gov ties","government_ties")):
                chunks.append(_bool_chip(label, details.get(key)))
            chunks.append('</div><div class="profile-mini-heading">VOLUNTARY SELF-ID</div>')
            chunks.append(f'<div class="profile-default-row"><span>Gender</span><strong>{esc(details.get("gender"), "Not answered")}</strong></div>')
            chunks.append(f'<div class="profile-default-row"><span>Ethnicity</span><strong>{esc(details.get("ethnicity"), "Not answered")}</strong></div><div class="profile-chips">')
            chunks.append(_bool_chip("Veteran", details.get("veteran")))
            chunks.append(_bool_chip("Disability", details.get("disability")))
            chunks.append('</div></div>')

            skills = profile.get("skills") or []
            chunks.append('<div class="profile-right-card">' + _section_header("‹›", "Skills", f"{sum(len(group.get('skills') or []) for group in skills)} skills across {len(skills)} categories", "skill"))
            for group in skills:
                chips = "".join(f'<span class="profile-chip">{esc(value)}</span>' for value in group.get("skills") or [])
                chunks.append(f'<div class="profile-skill-group"><div class="profile-skill-label">{esc(group.get("category"), "Other")}</div><div class="profile-chips">{chips}</div></div>')
            chunks.append('</div>')

            certs = profile.get("certifications") or []
            chunks.append('<div class="profile-right-card">' + _section_header("✹", "Certifications", f"{len(certs)} cert{'s' if len(certs)!=1 else ''}", "cert"))
            for cert in certs:
                issuer = str(cert.get("issuer") or "Issuer not recorded")
                asset = _asset_for(assets, issuer, "certification")
                meta = " · ".join(str(value) for value in (issuer, cert.get("date")) if value)
                chunks.append(f'<div class="profile-cert-card">{_logo_html(asset,issuer)}<div><div class="profile-cert-name">{esc(cert.get("name"), "Certification")}</div><div class="profile-cert-issuer">{esc(meta)}</div></div></div>')
            chunks.append('</div>')
            languages = profile.get("languages") or []
            if languages:
                chunks.append('<div class="profile-right-card">' + _section_header("A", "Languages", f"{len(languages)} recorded", "", edit=False) + '<div class="profile-chips">' + "".join(f'<span class="profile-chip gray">{esc(value)}</span>' for value in languages) + '</div></div>')
            st.markdown("".join(chunks), unsafe_allow_html=True)


def _resume_tab() -> None:
    source = v3.active_source()
    designated = master_resume()
    st.markdown("### Resume")
    if source:
        with st.container(border=True):
            st.markdown(f"**{esc(source.get('label'), 'Master Resume')}**", unsafe_allow_html=True)
            st.caption(f"Confirmed Resume Studio source · SHA-256 {str(source.get('content_sha256') or '')[:12]}…")
            st.markdown('<a class="profile-edit-link" href="?view=resume-studio" target="_self">Open Resume Studio →</a>', unsafe_allow_html=True)
    elif designated:
        with st.container(border=True):
            st.markdown(f"**{esc(designated.get('label'), 'Master resume')}**", unsafe_allow_html=True)
            if safe_link(designated.get("url")):
                st.link_button("Open master resume", safe_link(designated["url"]), type="primary")
    else:
        st.markdown('<div class="profile-empty"><h3>No confirmed Master Resume yet</h3><p>Upload and confirm one in Resume Studio to initialize your MUNSHI profile.</p><a class="profile-edit-link" href="?view=resume-studio" target="_self">Open Resume Studio →</a></div>', unsafe_allow_html=True)
    records = [row for row in tracker_rows(limit=250) if safe_link(row.get("resume_pdf_url"))]
    if records:
        st.markdown("### Tailored resume history")
        for row in records[:40]:
            with st.container(border=True):
                left, right = st.columns((3.2, 1))
                with left:
                    st.markdown(f"**{esc(row.get('company_name'), 'Company')} · {esc(row.get('title'), 'Role')}**", unsafe_allow_html=True)
                    st.caption(f"ATS score: {row.get('final_ats_score') if row.get('final_ats_score') is not None else 'Not available'}")
                with right:
                    st.link_button("Open resume", safe_link(row["resume_pdf_url"]), use_container_width=True)


def _cover_letter_tab() -> None:
    records = [row for row in tracker_rows(limit=250) if safe_link(row.get("cover_letter_doc_url"))]
    st.markdown("### Cover Letter")
    if not records:
        st.markdown('<div class="profile-empty"><h3>No cover letters yet</h3><p>Generated cover-letter artifacts will appear here after the guarded preparation workflow records them.</p></div>', unsafe_allow_html=True)
        return
    for row in records[:40]:
        with st.container(border=True):
            left, right = st.columns((3.2, 1))
            with left:
                st.markdown(f"**{esc(row.get('company_name'), 'Company')} · {esc(row.get('title'), 'Role')}**", unsafe_allow_html=True)
                st.caption(str(row.get("completed_at") or row.get("sent_at") or "Date not recorded"))
            with right:
                st.link_button("Open cover letter", safe_link(row["cover_letter_doc_url"]), use_container_width=True)


def render() -> None:
    _inject_css()
    tab = _tab_from_state()
    _subnav(tab)
    if tab == "Resume":
        _resume_tab()
        return
    if tab == "Cover Letter":
        _cover_letter_tab()
        return
    extracted = _latest_confirmed_profile()
    if not extracted:
        st.markdown(
            '<div class="profile-empty"><h3>Your permanent MUNSHI profile is not initialized yet</h3><p>Upload a Master Resume, extract the structured profile, review it, and confirm it in Resume Studio. The confirmed profile will then live here and will not require re-uploading for each job.</p><a class="profile-edit-link" href="?view=resume-studio" target="_self">Initialize profile in Resume Studio →</a></div>',
            unsafe_allow_html=True,
        )
        return
    profile = extracted.get("profile") or {}
    details = load_candidate_profile_details()
    _render_profile_details(profile, details)
