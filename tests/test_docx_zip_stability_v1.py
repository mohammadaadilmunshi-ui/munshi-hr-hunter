"""DOCX immutable artifact byte stability across clock and ZIP timestamp changes."""
from __future__ import annotations

import hashlib
import io
import zipfile

from app.native_resume_service import render_docx_bytes
from tests.test_native_resume_stage_b_coverage_v1 import _document

EXPECTED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/styles.xml",
    "word/_rels/document.xml.rels",
}


def test_docx_zip_metadata_is_deterministic_without_changing_resume_xml(monkeypatch):
    source = _document()
    one = render_docx_bytes(source)
    # Any future date, locale or rounding boundary must not change DOCX bytes.
    monkeypatch.setattr(
        zipfile.time, "localtime",
        lambda *args, **kwargs: (2038, 7, 9, 13, 14, 58, 0, 0, 0),
    )
    two = render_docx_bytes(source)
    assert one == two
    assert hashlib.sha256(one).digest() == hashlib.sha256(two).digest()
    with zipfile.ZipFile(io.BytesIO(one), "r") as z:
        assert set(z.namelist()) == EXPECTED_PARTS
        assert all(x.date_time == (1980, 1, 1, 0, 0, 0) for x in z.infolist())
        assert all(x.compress_type == zipfile.ZIP_DEFLATED for x in z.infolist())
        document = z.read("word/document.xml").decode("utf-8")
        styles = z.read("word/styles.xml").decode("utf-8")
        assert "Example Candidate" in document
        assert "Built workforce dashboards and reporting workflows." in document
        assert '<w:pgSz w:w="12240" w:h="15840"/>' in document
        assert 'w:ascii="Arial"' in styles


def test_docx_xml_differs_when_truth_bound_content_changes():
    unchanged = _document()
    first = render_docx_bytes(unchanged)
    altered = unchanged.model_copy(deep=True)
    altered.candidate_name = "Different Candidate"
    second = render_docx_bytes(altered)
    assert first != second
    with zipfile.ZipFile(io.BytesIO(second)) as z:
        assert "Different Candidate" in z.read("word/document.xml").decode()
