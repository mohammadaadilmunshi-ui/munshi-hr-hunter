"""Default to aggressive for new user sessions; preserve explicit alternatives."""
import inspect
from pathlib import Path
from app import native_resume_service_v5 as v5
from app import prepare_worker_adapters_v1a as worker
from app import resume_engine_selector as selector


def test_all_new_resume_generation_defaults_aggressive():
    assert inspect.signature(v5.generate_resume).parameters["rewrite_mode"].default=="aggressive"
    assert inspect.signature(worker.run_native_resume_once).parameters["rewrite_mode"].default=="aggressive"
    assert inspect.signature(selector._run_native).parameters["rewrite_mode"].default=="aggressive"


def test_resume_studio_defaults_do_not_change_past_explicit_selection():
    root=Path(__file__).resolve().parent.parent
    v2=(root/"app/resume_studio_page_v2.py").read_text()
    v4=(root/"app/resume_studio_page_v4.py").read_text()
    engine=(root/"app/resume_engine_selector.py").read_text()
    assert 'def _rewrite_selector(key: str, default: str = "aggressive")' in v2
    assert 'def _style_selector(key: str, default: str = "aggressive")' in v4
    assert 'prior if prior in STYLE_LABELS else "aggressive"' in v4
    assert 'native_mode = "aggressive"' in engine
    assert 'index=2,' in engine
