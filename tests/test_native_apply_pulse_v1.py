"""MUNSHI Apply Pulse — evidence-bound readiness, never submission odds."""
from app.job_destination_v1 import JobDestination
from app.native_apply_pulse_v1 import apply_pulse, slug, PULSE_VERSION


def destination(provider="Paycom", host="careers.paycomonline.net", confidence="stored"):
    return JobDestination(provider=provider,apply_via=provider or "Employer website",
        source="LinkedIn",original_url=("https://"+host+"/job/124") if host else None,
        destination_domain=host or None,confidence=confidence,evidence="stored_provider")


def snapshot(*, status=10, forms=0, controls=(), receipts=0, claimed=True):
    data={"provider":"Paycom","public_vendor_claim":claimed,
          "distinct_application_refs":status,"distinct_form_application_refs":forms,
          "form_observations":forms*3,"observed_control_kinds":list(controls)}
    return {"aliases":{"paycom":"paycom"},"providers":{"paycom":data},
            "native_receipts":{"paycom":receipts}}


def test_native_apply_pulse_is_not_hiring_or_submission_probability():
    result=apply_pulse(destination(),snapshot())
    assert result["version"]==PULSE_VERSION
    assert result["is_submission_probability"] is False
    assert "not a guarantee" in result["description"]
    assert result["percent"] is not None


def test_more_real_form_evidence_increases_pulse():
    early=apply_pulse(destination(),snapshot(status=10,forms=0))
    trained=apply_pulse(destination(),snapshot(status=10,forms=6,controls=("text","select","file")))
    assert early["percent"] < trained["percent"]
    assert trained["stage"]=="Well practiced"
    assert trained["percent"] <= 86


def test_verified_native_employer_receipt_adds_confidence_without_invention():
    base=apply_pulse(destination(),snapshot(forms=6,receipts=0))
    verified=apply_pulse(destination(),snapshot(forms=6,receipts=3))
    assert verified["percent"]>base["percent"]
    assert verified["stage"]=="Live-tested"
    assert verified["native_receipts"]==3


def test_relay_marketing_does_not_change_native_score():
    claimed=apply_pulse(destination(),snapshot(claimed=True))
    unclaimed=apply_pulse(destination(),snapshot(claimed=False))
    assert claimed["percent"]==unclaimed["percent"]


def test_no_employer_destination_uses_positive_non_numeric_label():
    missing=destination("Workday","",confidence="unknown")
    result=apply_pulse(missing,snapshot(status=80,forms=25,receipts=15))
    assert result["percent"] is None
    assert result["stage"]=="Gathering"
    assert result["native_receipts"]==0


def test_aggregator_url_does_not_masquerade_as_employer_destination():
    result=apply_pulse(destination("Workday","linkedin.com"),snapshot(forms=10))
    assert result["percent"] is None


def test_unknown_employer_gets_base_index_without_imaginary_form_history():
    result=apply_pulse(destination(None,"careers.example.org",confidence="domain_only"),
                       {"providers":{},"aliases":{},"native_receipts":{}})
    assert result["percent"]==52
    assert result["stage"]=="Exploring"


def test_calculation_is_deterministic_and_capped():
    signals=snapshot(status=10000000,forms=10000000,controls=("text","select","file","date"),receipts=9999)
    a=apply_pulse(destination(),signals)
    b=apply_pulse(destination(),signals)
    assert a==b
    assert a["percent"]<=96


def test_slug_normalizes_ats_provider_names():
    assert slug("SAP SuccessFactors")==slug("SAP-successfactors")
