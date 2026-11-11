"""Hotfix for Resume Studio API-key field clearing after an encrypted save.

Streamlit forbids mutating a widget-owned session-state key after that widget has
already been instantiated in the current run. Resume Studio V2 saved the key
successfully and then attempted to clear the text-input key in the same run,
which raised StreamlitAPIException and exposed a traceback to the user.

This additive repair preserves the existing save path. It catches only that one
known post-save exception, schedules the field clear for the next rerun, and
then clears it before the widget is instantiated again.
"""
from __future__ import annotations

import streamlit as st
from streamlit.errors import StreamlitAPIException

from app import resume_studio_page_v2 as v2page


_CLEAR_FLAG = "_munshi_resume_studio_clear_api_key_after_save"
_SUCCESS_FLAG = "_munshi_resume_studio_api_key_saved_toast"
_TARGET_FRAGMENT = (
    "native_resume_v2_api_key cannot be modified after the widget with key "
    "native_resume_v2_api_key is instantiated"
)


def install_resume_studio_session_state_repair_v1() -> None:
    """Install the narrow post-save Streamlit session-state repair once."""
    if getattr(v2page, "_munshi_session_state_repair_v1_installed", False):
        return

    original = v2page._writer_settings_panel

    def repaired_writer_settings_panel():
        # This executes before v2page creates the password widget on each rerun.
        if st.session_state.pop(_CLEAR_FLAG, False):
            st.session_state["native_resume_v2_api_key"] = ""
        if st.session_state.pop(_SUCCESS_FLAG, False):
            st.toast("Personal OpenAI API key saved securely.", icon="✅")

        try:
            return original()
        except StreamlitAPIException as error:
            # The encrypted save has already completed at this exact point in
            # the legacy V2 panel. Never call the save function a second time.
            if _TARGET_FRAGMENT not in str(error):
                raise
            st.session_state[_CLEAR_FLAG] = True
            st.session_state[_SUCCESS_FLAG] = True
            st.rerun()
            raise  # pragma: no cover - st.rerun terminates the script run

    v2page._writer_settings_panel = repaired_writer_settings_panel
    v2page._munshi_session_state_repair_v1_installed = True
