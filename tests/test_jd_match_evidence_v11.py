"""More useful lexical JD matches, with no unsupported or protected inference."""
from app import jd_requirement_match_v1 as matcher


def evidence(text):
    return [{
        "evidence_id":"truth:verified", "fact_key":"experience.0.bullets.0",
        "normalized_text":text.casefold(),
        "tokens":matcher._evidence_terms(text),
    }]


def resolve(text, supported, kind="RESPONSIBILITY"):
    return matcher._requirement_evidence_match({
        "requirement_id":"job-req", "type":kind,
        "priority":"MUST_HAVE", "exact_text":text,
    }, evidence(supported))


def test_confidentiality_and_documentation_are_supported_by_source():
    result=resolve(
        "Must always maintain internal and external confidentiality",
        "Maintained confidential employee documentation"
    )
    assert result["match_status"] in ("PARTIAL","STRONG_TRANSFERABLE","DIRECT")
    assert "truth:verified" in result["evidence_ids"]
    assert matcher._evidence_terms("onboarding and recruiting") == {
        "onboard","recruit"
    }


def test_cannot_invent_unlisted_ats_tool_or_skill():
    result=resolve(
        "Proficiency in Oracle HCM and SAP SuccessFactors",
        "Coordinated recruiting and employee onboarding at Toyota"
    )
    assert result["match_status"]=="NO_EVIDENCE"


def test_immigration_and_clearance_are_never_inferred():
    for kind in ("WORK_AUTHORIZATION","SPONSORSHIP","CITIZENSHIP","CLEARANCE"):
        result=resolve("Must have required authorization", "Managed onboarding", kind)
        assert result["match_status"]=="UNKNOWN"
        assert result["evidence_ids"]==[]
