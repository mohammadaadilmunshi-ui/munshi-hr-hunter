from app import application_review_card_v1 as card


def _resume(*, parent=False):
    return {
        "document": {
            "summary": {
                "text": "HR analytics professional with recruitment and resume screening experience."
            },
            "skills": [
                {
                    "label": "HR",
                    "skills": ["Recruitment", "Resume Screening", "HR Database"],
                }
            ],
            "experience": [
                {
                    "organization": "Example",
                    "title": "HR Intern",
                    "bullets": [
                        {"text": "Screened resumes and supported recruitment operations."},
                        {"text": "Maintained an HR database and onboarding records."},
                    ],
                }
            ],
            "projects": [],
        },
        "diagnostics": {
            "jd_supported_requirement_coverage_percent": 69.0,
            "jd_supported_requirement_count": 13,
            "jd_supported_requirement_represented_count": 9,
            "stage_b": {
                "supported_requirement_coverage": {
                    "percent": 69.0,
                    "supportable_requirement_count": 13,
                    "represented_requirement_count": 9,
                    "represented_requirement_ids": ["r1", "r2"],
                }
            },
        },
        "parent_version_id": "parent" if parent else None,
    }


def _plan():
    return {
        "supported_jd_terms": [
            "recruitment",
            "resume screening",
            "job posting",
            "HR database",
        ],
        "skills_priority_requirement_ids": ["r1", "r2", "r3"],
        "requirement_refs": [
            {
                "requirement_id": "r1",
                "type": "SKILL",
                "priority": "MUST_HAVE",
                "exact_text": "Recruitment",
                "match_status": "SUPPORTED",
            },
            {
                "requirement_id": "r2",
                "type": "PROCESS",
                "priority": "CORE_RESPONSIBILITY",
                "exact_text": "Resume screening",
                "match_status": "SUPPORTED",
            },
            {
                "requirement_id": "r3",
                "type": "SKILL",
                "priority": "MUST_HAVE",
                "exact_text": "Job posting",
                "match_status": "SUPPORTED",
            },
        ],
    }


def test_review_card_projects_real_coverage_and_one_page():
    result = card.build_review_card(
        resume_record=_resume(),
        plan=_plan(),
        page_verification={"status": "PASS", "pdf_pages": 1},
    )

    assert result["coverage_percent"] == 69.0
    assert result["coverage_label"] == "Strong match"
    assert result["represented_requirement_count"] == 9
    assert result["supportable_requirement_count"] == 13
    assert result["covered_keyword_count"] == 3
    assert result["keyword_count"] == 4
    assert result["one_page"] is True

    required = {item["text"]: item["covered"] for item in result["required_skills"]}
    assert required == {
        "Recruitment": True,
        "Resume screening": True,
        "Job posting": False,
    }


def test_review_card_reports_tailored_changes_without_authority():
    result = card.build_review_card(
        resume_record=_resume(),
        plan=_plan(),
        page_verification={"status": "PASS", "pdf_pages": 1},
    )

    assert result["changed_bullet_count"] == 2
    assert result["added_skill_count"] == 3
    assert result["summary"].startswith("HR analytics professional")
    assert "submission_authority" not in result
    assert "approval" not in result


def test_revision_diff_counts_only_new_content():
    parent = _resume()
    current = _resume(parent=True)
    current["document"]["skills"][0]["skills"].append("Job Posting")
    current["document"]["experience"][0]["bullets"].append(
        {"text": "Created job postings for open roles."}
    )

    result = card.build_review_card(
        resume_record=current,
        plan=_plan(),
        page_verification={"status": "PASS", "pdf_pages": 1},
        parent_record=parent,
    )

    assert result["is_revision"] is True
    assert result["changed_bullet_count"] == 1
    assert result["changed_bullets"] == ["Created job postings for open roles."]
    assert result["added_skill_count"] == 1
    assert result["added_skills"] == ["Job Posting"]

def test_long_quoted_jd_sentence_is_not_misrepresented_as_keyword_zero():
    sample = _resume()
    sample["diagnostics"]["stage_b"]["supported_requirement_coverage"] = {
        "percent": 100.0,
        "supportable_requirement_count": 2,
        "represented_requirement_count": 2,
        "represented_requirement_ids": ["r1", "r2"],
    }
    plan = _plan()
    plan["supported_jd_terms"] = [
        '"Excellent organizational and documentation skills"'
    ]
    plan["requirement_refs"] = [
        {"requirement_id": f"r{i}", "type": "OTHER", "priority": "UNKNOWN",
         "match_status": "UNSUPPORTED", "exact_text": "some other JD requirement"}
        for i in range(47)
    ]
    result=card.build_review_card(resume_record=sample,plan=plan)
    assert result["keyword_count"] == 0
    assert result["covered_keyword_count"] == 0
    assert result["coverage_label"] == "Limited evidence"
    assert result["evidence_limited"] is True
    assert result["jd_requirement_count"] == 47
