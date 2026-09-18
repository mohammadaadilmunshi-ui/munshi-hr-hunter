from __future__ import annotations

import json
from pathlib import Path

from app.product_v22 import _safe_linkedin_url
from app.universal_n8n_progress import recruiter_contacts_from_result


def test_recruiter_contacts_public_helper_reads_persisted_n8n_shapes() -> None:
    result = {
        "recruiter_contacts_json": json.dumps(
            [
                {
                    "name": "Taylor Recruiter",
                    "title": "Talent Partner",
                    "email": "taylor@example.com",
                    "linkedin_url": "https://www.linkedin.com/in/taylor-recruiter",
                }
            ]
        )
    }
    contacts = recruiter_contacts_from_result(result)
    assert contacts == [
        {
            "name": "Taylor Recruiter",
            "linkedin": "https://www.linkedin.com/in/taylor-recruiter",
            "email": "taylor@example.com",
            "title": "Talent Partner",
        }
    ]


def test_linkedin_url_guard_accepts_only_linkedin_http_urls() -> None:
    assert (
        _safe_linkedin_url("https://www.linkedin.com/in/taylor")
        == "https://www.linkedin.com/in/taylor"
    )
    assert _safe_linkedin_url("javascript:alert(1)") is None
    assert _safe_linkedin_url("https://example.com/in/taylor") is None


def test_product_opportunity_dialog_wires_recruiter_section_before_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_v22.py").read_text(encoding="utf-8")
    dialog_start = source.index("def _opportunity_dialog(job_id: int) -> None:")
    dialog_end = source.find("\ndef ", dialog_start + 1)
    dialog = source[dialog_start:] if dialog_end == -1 else source[dialog_start:dialog_end]

    assert "Recruiter / hiring contacts" in source
    assert "recruiter_contacts_json" in source
    assert "recruiter_linkedin_urls_json" in source
    assert "_render_recruiter_contacts(job_id)" in dialog
    assert 'st.markdown("### Actions")' in dialog
    assert dialog.index("_render_recruiter_contacts(job_id)") < dialog.index(
        'st.markdown("### Actions")'
    )
    assert "Open LinkedIn" in source
