"""Compatibility import for the corrected canonical application catalog.

V2 preserves the public V1 functions/types while fixing the initial catalog-size
mistake and tightening United States wording normalization. New code may import
V2 directly; existing callers remain stable through this module.
"""
from app.application_question_catalog_v2 import *  # noqa: F403
