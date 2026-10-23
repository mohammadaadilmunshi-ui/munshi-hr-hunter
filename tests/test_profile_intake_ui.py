from streamlit.testing.v1 import AppTest


def test_first_source_requires_text_and_explicit_confirmation():
    app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from app.master_resume_visual_ui_v2 import render_intake
def save(**kwargs):
 st.session_state["source_saves"] = st.session_state.get("source_saves",0)+1
with patch("app.native_resume_service.save_confirmed_source",side_effect=save):
 render_intake()
''').run()
    assert not app.exception
    app.button[0].click().run()
    assert "source_saves" not in app.session_state
    assert "Add your resume text" in app.error[0].value
    app.text_area(key="profile_intake_text").input("TEST/STAGING Alex Example\nHR Operations Analyst").run()
    app.button[0].click().run()
    assert "source_saves" not in app.session_state
    assert "truth confirmation" in app.error[0].value
    app.checkbox(key="profile_intake_confirmed").check().run()
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["source_saves"] == 1
