from __future__ import annotations

from pathlib import Path


def test_resume_studio_post_save_session_state_hotfix_is_installed() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    repair = (root / "app" / "resume_studio_session_state_repair_v1.py").read_text(encoding="utf-8")

    assert "install_resume_studio_session_state_repair_v1" in shell
    assert "install_resume_studio_session_state_repair_v1()" in shell

    assert "except StreamlitAPIException as error" in repair
    assert "native_resume_v2_api_key cannot be modified after the widget with key" in repair
    assert 'st.session_state["native_resume_v2_api_key"] = ""' in repair
    assert "st.session_state.pop(_CLEAR_FLAG, False)" in repair
    assert "st.rerun()" in repair


def test_resume_studio_hotfix_does_not_repeat_the_encrypted_save() -> None:
    root = Path(__file__).resolve().parents[1]
    repair = (root / "app" / "resume_studio_session_state_repair_v1.py").read_text(encoding="utf-8")

    # The legacy panel has already saved the encrypted key before the exact
    # Streamlit post-widget mutation exception is raised. The repair must only
    # schedule a safe clear/rerun and must never write the credential again.
    assert "save_personal_api_key" not in repair
    assert "delete_personal_api_key" not in repair


def test_resume_studio_hotfix_clears_before_original_panel_renders() -> None:
    root = Path(__file__).resolve().parents[1]
    repair = (root / "app" / "resume_studio_session_state_repair_v1.py").read_text(encoding="utf-8")

    clear_index = repair.index('st.session_state["native_resume_v2_api_key"] = ""')
    original_index = repair.index("return original()")
    assert clear_index < original_index
