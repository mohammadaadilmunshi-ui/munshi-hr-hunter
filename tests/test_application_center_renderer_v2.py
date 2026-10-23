from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _text(app: AppTest) -> str:
    values = []
    for kind in ("markdown", "caption", "info", "warning", "success", "title", "header", "subheader"):
        for element in getattr(app, kind):
            values.append(str(element.value))
    return "\n".join(values)


def test_application_center_renders_multiple_states_without_default_selection(tmp_path: Path) -> None:
    script = tmp_path / "application_center_fixture.py"
    script.write_text(
        """
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from app import application_view_model_v1 as vm
from app import unified_job_workspaces_v1 as workspaces

def model(job_id, state, group, action, *, degraded=False):
    return vm.ApplicationViewModel(
        job_id=job_id, application_id=f'application-{job_id}',
        company=f'Company {job_id}', title=f'Role {job_id}',
        location='Philadelphia, PA', work_arrangement='Hybrid', match=90-job_id,
        canonical_state=state, human_state={
            'preparing':'Preparing', 'needs_you':'Needs review',
            'needs_attention':'Needs attention', 'submitted':'Submitted',
            'applying':'Applying now'}[group],
        state_group=group, last_update='2026-09-22T10:00:00+00:00',
        next_action=action, safe_progress_stage='Resume tailoring',
        stage_index=2, stage_count=6, resume_review_available=group=='needs_you',
        protected_checkpoint=False,
        issue_summary='Stopped safely.' if group=='needs_attention' else None,
        stopped_at='Package checks' if group=='needs_attention' else None,
        submission_attempted=False,
        receipt_summary={'provider':'Greenhouse'} if group=='submitted' else None,
        worker_heartbeat={'state':'healthy','label':'Worker healthy'},
        stale=False, allowed_recovery_actions=('refresh_state','clear_local_selection'),
        degraded=degraded, projection_error='FixtureError' if degraded else None,
    )

views = [
    model(1,'RUNNING','preparing','No action needed'),
    model(2,'READY_FOR_APPROVAL','needs_you','Review resume'),
    model(3,'FAILED_SAFELY','needs_attention','Review the issue', degraded=True),
    model(4,'VERIFIED','submitted','View receipt'),
    model(5,'SUBMITTING','applying','No action needed'),
]
pages = SimpleNamespace(_show_action_feedback=lambda: None)
workspace = SimpleNamespace(_render_complete_loop=lambda *args, **kwargs: st.write('resume review'))
with patch.object(vm, 'application_views', lambda limit=250: views), patch.object(
    vm, 'view_for_job', lambda job_id: next(item for item in views if item.job_id == job_id)
):
    workspaces.render_applications(pages, workspace)
""",
        encoding="utf-8",
    )

    app = AppTest.from_file(str(script), default_timeout=30).run()
    rendered = _text(app)

    assert not app.exception
    for value in ("Preparing", "Needs review", "Needs attention", "Submitted", "Applying now"):
        assert value in rendered
    assert "Select an application" in rendered
    assert "Company 3" in rendered


def test_needs_review_opens_resume_only_compact_review(tmp_path: Path) -> None:
    script = tmp_path / "application_review_fixture.py"
    script.write_text(
        """
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from app import application_view_model_v1 as vm
from app import unified_job_workspaces_v1 as workspaces

review = vm.ApplicationViewModel(
    job_id=22, application_id='application-22', company='Acme', title='People Analyst',
    location='Remote', work_arrangement='Remote', match=94,
    canonical_state='READY_FOR_APPROVAL', human_state='Needs review', state_group='needs_you',
    last_update='2026-09-22T10:00:00+00:00', next_action='Review resume',
    safe_progress_stage='Needs review', stage_index=4, stage_count=6,
    resume_review_available=True, protected_checkpoint=False, issue_summary=None,
    stopped_at=None, submission_attempted=False, receipt_summary=None,
    worker_heartbeat={'state':'healthy','label':'Worker healthy','recorded_at':'2026-09-22T10:00:00+00:00'},
    stale=False, allowed_recovery_actions=('refresh_state','clear_local_selection'),
)
st.query_params['job'] = '22'
pages = SimpleNamespace(_show_action_feedback=lambda: None)
def compact_review(job_id, *, compact=False):
    st.write(f'RESUME_ONLY_REVIEW:{job_id}:{compact}')
workspace = SimpleNamespace(_render_complete_loop=compact_review)
with patch.object(vm, 'application_views', lambda limit=250: [review]), patch.object(
    vm, 'view_for_job', lambda job_id: review
):
    workspaces.render_applications(pages, workspace)
""",
        encoding="utf-8",
    )

    app = AppTest.from_file(str(script), default_timeout=30).run()

    assert not app.exception
    assert "RESUME_ONLY_REVIEW:22:True" in _text(app)
    assert "Approve again" not in _text(app)


def test_auto_prepare_control_center_renders_real_per_job_groups(tmp_path: Path) -> None:
    script = tmp_path / "auto_prepare_fixture.py"
    script.write_text(
        """
from types import SimpleNamespace
from unittest.mock import patch
from app import application_view_model_v1 as vm
from app import auto_prepare_readiness_ui_v1 as readiness
from app import product_pages

def item(
    job_id,
    company,
    title,
    group,
    state,
    action,
    *,
    resume_review_available=False,
):
    return SimpleNamespace(
        job_id=job_id, application_id=f'application-{job_id}',
        company=company, title=title, state_group=group,
        human_state=state, next_action=action, location='New York, NY',
        safe_progress_stage='Package checks', last_update='2026-09-22 10:00:00',
        resume_review_available=resume_review_available,
    )

snapshot = {
    'applications': [
        item(
            1,'Acme','Analyst','needs_you','Needs review','Review resume',
            resume_review_available=True,
        ),
        item(2,'Beta','Coordinator','preparing','Preparing','No action needed'),
        item(3,'Gamma','Specialist','applying','Applying now','No action needed'),
        item(4,'Delta','Partner','needs_attention','Needs attention','Review the issue'),
        item(5,'Echo','Associate','submitted','Submitted','View receipt'),
        item(6,'Foxtrot','Advisor','needs_you','Action required','Complete protected checkpoint'),
    ],
    'counts': {'preparing':1,'needs_review':1,'applying':1,'needs_attention':1,'submitted_today':1},
    'worker': {'state':'healthy','label':'Worker healthy','recorded_at':'2026-09-22T10:00:00+00:00'},
}
with patch.object(vm, 'control_center_snapshot', lambda limit=250: snapshot), patch.object(
    readiness, 'readiness_snapshot', lambda: {'ready': True, 'missing': [], 'values': {}}
), patch.object(
    product_pages, 'volume_policy', lambda: {'mode':'unlimited','daily_limit':None,'review_first':False}
):
    product_pages.auto_prepare()
""",
        encoding="utf-8",
    )

    app = AppTest.from_file(str(script), default_timeout=30).run()
    rendered = _text(app)

    assert not app.exception
    for value in (
        "Auto Prepare: On",
        "Worker healthy",
        "Needs review",
        "Needs your input",
        "Preparing now",
        "Applying now",
        "Needs attention",
        "Recently submitted",
    ):
        assert value in rendered
    # The control-center summary and the individual application can each
    # mention the same actionable status; duplicate copy is not a duplicate
    # application, so do not require an exact text count.
    assert rendered.count("Needs review") >= 1


def test_sqlite_read_contention_uses_last_good_application_projection(tmp_path: Path) -> None:
    script = tmp_path / "application_center_contention_fixture.py"
    script.write_text(
        """
from types import SimpleNamespace
from unittest.mock import patch
import sqlite3
import streamlit as st
from app import application_view_model_v1 as vm
from app import unified_job_workspaces_v1 as workspaces

cached = vm.ApplicationViewModel(
    job_id=31, application_id='application-31', company='Cached Company', title='HR Analyst',
    location='Remote', work_arrangement='Remote', match=91,
    canonical_state='RUNNING', human_state='Preparing', state_group='preparing',
    last_update='2026-09-22T10:00:00+00:00', next_action='No action needed',
    safe_progress_stage='Resume tailoring', stage_index=2, stage_count=6,
    resume_review_available=False, protected_checkpoint=False, issue_summary=None,
    stopped_at=None, submission_attempted=False, receipt_summary=None,
    worker_heartbeat={'state':'healthy','label':'Worker healthy'}, stale=False,
    allowed_recovery_actions=('refresh_state','clear_local_selection'),
)
st.session_state['_application_center_last_good'] = [cached]
pages = SimpleNamespace(_show_action_feedback=lambda: None)
workspace = SimpleNamespace(_render_complete_loop=lambda *args, **kwargs: None)
with patch.object(
    vm,
    'application_views',
    lambda limit=250: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')),
):
    workspaces.render_applications(pages, workspace)
""",
        encoding="utf-8",
    )

    app = AppTest.from_file(str(script), default_timeout=30).run()
    rendered = _text(app)

    assert not app.exception
    assert "Live application state is temporarily busy" in rendered
    assert "Cached Company" in rendered


def test_auto_prepare_deep_links_exact_application_identity() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "product_pages.py").read_text(
        encoding="utf-8"
    )
    auto_prepare = source[source.index("def auto_prepare()"):source.index("\ndef ", source.index("def auto_prepare()") + 20)]
    assert 'application_url = f"?view=prepare-application&job={item.job_id}"' in auto_prepare
    assert 'application_url += f"&application={item.application_id}"' in auto_prepare


def test_auto_prepare_page_does_not_rescan_full_eligible_pool() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "product_pages.py").read_text(
        encoding="utf-8"
    )
    start = source.index("def auto_prepare()")
    end = source.index("\ndef ", start + 20)
    auto_prepare = source[start:end]
    assert "eligible_unprepared_count" not in auto_prepare
    assert 'metrics[1].metric("Admission", admission_status)' in auto_prepare
    assert "Eligibility is evaluated by the supervised Netcup worker" in auto_prepare
