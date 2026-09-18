"""First-class product Settings projection for canonical job targeting policy.

This is a user-facing wrapper over the existing canonical targeting setting. It
does not create a second policy authority: saves go back to the same versioned
SQLite setting consumed by discovery, dashboard filtering, Telegram delivery,
scoring, and AutoPrepare.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.database import get_setting, save_setting


def _lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def targeting_snapshot() -> dict[str, Any]:
    targeting = dict(get_setting("targeting", {}) or {})
    eligibility = dict(targeting.get("eligibility") or {})
    experience = dict(targeting.get("experience_policy") or {})
    families = list(targeting.get("role_families") or [])
    return {
        "targeting": targeting,
        "eligibility": eligibility,
        "experience": experience,
        "families": families,
        "mode": str(targeting.get("mode") or "Not configured"),
        "label": str(eligibility.get("label") or "Not configured"),
    }


def render_targeting_settings() -> None:
    snapshot = targeting_snapshot()
    targeting = dict(snapshot["targeting"])
    eligibility = dict(snapshot["eligibility"])
    experience = dict(snapshot["experience"])
    families = list(snapshot["families"])

    st.divider()
    st.markdown(
        '<div id="job-search-targeting-and-eligibility"></div>',
        unsafe_allow_html=True,
    )
    st.markdown("### Job search targeting and eligibility")
    st.caption(
        "Canonical targeting shared by discovery, dashboard ranking, Telegram "
        "delivery, AutoPrepare, and the application pipeline. These controls "
        "change the existing versioned policy; they do not create a second rule set."
    )

    summary = st.columns(4, gap="small")
    summary[0].metric(
        "Authorization mode",
        "OPT (student work authorization)"
        if snapshot["mode"] == "OPT"
        else snapshot["mode"],
    )
    summary[1].metric("Eligibility", snapshot["label"])
    summary[2].metric("Role families", len(families))
    summary[3].metric(
        "Experience reject",
        f"{int(experience.get('reject_required_min_years', 2))}+ required years",
    )

    arrangements = []
    for key, label in (
        ("remote_allowed", "Remote"),
        ("hybrid_allowed", "Hybrid"),
        ("onsite_allowed", "Onsite"),
    ):
        if bool(eligibility.get(key)):
            arrangements.append(label)
    st.info(
        "Eligible geography: "
        + snapshot["label"]
        + " · Work arrangements: "
        + (", ".join(arrangements) if arrangements else "none")
        + " · Unknown country: "
        + str(eligibility.get("unknown_country_policy") or "reject")
    )

    with st.form("product_targeting_policy_v1"):
        st.markdown("#### United States eligibility")
        c1, c2, c3 = st.columns(3, gap="small")
        remote = c1.toggle(
            "Remote U.S.",
            value=bool(eligibility.get("remote_allowed", True)),
        )
        hybrid = c2.toggle(
            "Hybrid U.S.",
            value=bool(eligibility.get("hybrid_allowed", True)),
        )
        onsite = c3.toggle(
            "Onsite U.S.",
            value=bool(eligibility.get("onsite_allowed", True)),
        )

        unknown_policy = st.selectbox(
            "Unknown country policy",
            ["reject", "review"],
            index=0
            if str(eligibility.get("unknown_country_policy") or "reject") == "reject"
            else 1,
            help=(
                "Reject keeps discovery fail-closed when United States eligibility "
                "cannot be established."
            ),
        )

        required_years = st.number_input(
            "Reject when an explicitly required minimum reaches (years)",
            min_value=0,
            max_value=20,
            value=int(experience.get("reject_required_min_years", 2)),
            help=(
                "Only explicit required-minimum evidence is governed here. "
                "Preferred, optional, unknown, benefits, and boilerplate mentions "
                "do not trigger this threshold."
            ),
        )

        st.markdown("#### High-impact exclusions")
        x1, x2, x3 = st.columns(3, gap="small")
        title_rejects = x1.text_area(
            "Title-only seniority rejects",
            value="\n".join(targeting.get("title_only_hard_rejects") or []),
            height=190,
        )
        negative_contexts = x2.text_area(
            "Non-target role contexts",
            value="\n".join(targeting.get("role_negative_contexts") or []),
            height=190,
        )
        company_exclusions = x3.text_area(
            "Company exclusions",
            value="\n".join(targeting.get("company_exclusions") or []),
            height=190,
        )

        confirm = st.checkbox(
            "I reviewed these high-impact targeting changes",
            key="product_targeting_confirm_v1",
        )
        submitted = st.form_submit_button(
            "Save job search targeting",
            type="primary",
            disabled=not confirm,
        )

    if submitted:
        eligibility.update(
            remote_allowed=bool(remote),
            hybrid_allowed=bool(hybrid),
            onsite_allowed=bool(onsite),
            unknown_country_policy=str(unknown_policy),
        )
        experience["reject_required_min_years"] = int(required_years)
        targeting.update(
            eligibility=eligibility,
            experience_policy=experience,
            title_only_hard_rejects=_lines(title_rejects),
            role_negative_contexts=_lines(negative_contexts),
            company_exclusions=_lines(company_exclusions),
        )
        save_setting(
            "targeting",
            targeting,
            changed_by="streamlit:product-targeting-settings-v1",
        )
        st.success("Canonical job-search targeting saved with versioned history.")

    with st.expander("Role families and provider queries", expanded=False):
        if not families:
            st.info("No role families are configured.")
        else:
            st.dataframe(
                [
                    {
                        "Role family": str(family.get("name") or "Unnamed"),
                        "Eligible title phrases": len(family.get("title_phrases") or []),
                        "Provider queries": len(family.get("queries") or []),
                    }
                    for family in families
                ],
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "Detailed phrase/query editing remains available in Engineering "
                "diagnostics → Targeting. The product card above owns the common "
                "eligibility controls."
            )

