"""Safety contract: strong tailoring never silently deletes confirmed certificates."""
from types import SimpleNamespace
from app import native_resume_service_v5 as v5

def test_confirmed_certifications_remain_verbatim_regardless_of_job(monkeypatch):
    existing={
        "certifications":[{"name":"Only last certificate","issuer":"Issuer","evidence_ids":["source:old"]}],
    }
    monkeypatch.setattr(v5.v4.v1.ResumeDocument,"model_validate",
                        lambda payload: payload)
    result=v5._retain_confirmed_certifications(
        SimpleNamespace(model_dump=lambda: existing),
        {"facts":[
            {"key":"certifications.0.name","fact_id":"001","value":"Excel Job Simulation - Goldman Sachs"},
            {"key":"certifications.1.name","fact_id":"002","value":"HR Skills - OpenLearn"},
            {"key":"certifications.2.name","fact_id":"003","value":"BUS301: Human Resource Management - Saylor Academy"},
        ]},
    )
    assert [c["name"] for c in result["certifications"]] == [
        "Excel Job Simulation - Goldman Sachs","HR Skills - OpenLearn",
        "BUS301: Human Resource Management - Saylor Academy",
    ]
    assert [c["evidence_ids"] for c in result["certifications"]] == [
        ["truth:001"],["truth:002"],["truth:003"],
    ]

def test_without_confirmed_certification_entries_preserve_writer_output():
    doc=SimpleNamespace(model_dump=lambda: {})
    assert v5._retain_confirmed_certifications(doc,{"facts":[]}) is doc
