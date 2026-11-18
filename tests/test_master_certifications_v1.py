"""Lossless confirmed certificates, incl. broken PDF wrap at JP/Morgan."""
from types import SimpleNamespace
from app.master_certifications_v1 import complete_certifications
from app import deterministic_profile_extractor_v1 as v1
from app import native_resume_service_v5 as v5


def _wrapped_facts():
    return {"facts":[
        {"key":"certifications.0.name","fact_id":"a",
         "value":"Excel Skills for Business Job Simulation - Goldman Sachs | Investment Banking Job Simulation, Financial Analysis Track - JP"},
        {"key":"certifications.1.name","fact_id":"b",
         "value":"Morgan | Developing Your Skills as an HR Professional - OpenLearn | Strategic Human Resource Management - OpenLearn |"},
        {"key":"certifications.2.name","fact_id":"c",
         "value":"BUS301: Human Resource Management - Saylor Academy"},
    ]}

def test_confirmed_pdf_wrap_becomes_five_complete_certifications():
    result=complete_certifications(_wrapped_facts())
    assert len(result)==5
    assert [(c["name"],c["issuer"]) for c in result]==[
        ("Excel Skills for Business Job Simulation","Goldman Sachs"),
        ("Investment Banking Job Simulation, Financial Analysis Track","JP Morgan"),
        ("Developing Your Skills as an HR Professional","OpenLearn"),
        ("Strategic Human Resource Management","OpenLearn"),
        ("BUS301: Human Resource Management","Saylor Academy"),
    ]
    assert result[1]["evidence_ids"]==["truth:a","truth:b"]
    assert all(c["evidence_ids"] for c in result)

def test_source_extractor_joins_wrapped_pipe_delimiters_without_cutting_names():
    rows=v1._certifications([
        "Excel Skills for Business Job Simulation - Goldman Sachs | Investment Banking Job Simulation, Financial Analysis Track - JP",
        "Morgan | Developing Your Skills as an HR Professional - OpenLearn | What Is Strategic Human Resource Management? -",
        "OpenLearn | BUS301: Human Resource Management - Saylor Academy"
    ])
    assert len(rows)==5
    assert rows[1]["name"].endswith("JP Morgan")
    assert rows[3]["name"].endswith(" - OpenLearn")
    assert "Morgan Stanley" not in str(rows)

def test_writer_preserves_five_complete_verified_items(monkeypatch):
    monkeypatch.setattr(v5.v4.v1.ResumeDocument,"model_validate", lambda payload:payload)
    doc=v5._retain_confirmed_certifications(
        SimpleNamespace(model_dump=lambda:{"certifications":[]}),
        _wrapped_facts())
    assert len(doc["certifications"])==5
    assert doc["certifications"][1]["issuer"]=="JP Morgan"

def test_single_line_certificates_without_separators_are_not_renamed():
    facts={"facts":[{"key":"certifications.0.name","fact_id":"x",
                     "value":"Verified Name - Specific Issuer"}]}
    result=complete_certifications(facts)
    assert result==[{"name":"Verified Name - Specific Issuer",
                     "issuer":"","evidence_ids":["truth:x"]}]
