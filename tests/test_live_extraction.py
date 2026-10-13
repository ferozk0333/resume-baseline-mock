"""Live extraction checks against the real EXTRACT_MODEL (specs/07 §4, §6).

Opt-in: RIGHTSTAFF_LIVE=1 pytest tests/test_live_extraction.py
Costs a few cents per run (two Haiku calls).
"""
import os

import pytest

from core import config
from core.llm import ClaudeLLM
from extraction.extract import extract_document
from extraction.validate import SourceDoc, normalize_claim, validate_extraction
from ingest.documents import extract_text
from tests.helpers import INJECTION, SAMPLE_RESUME_LINES, make_pdf

pytestmark = pytest.mark.skipif(os.getenv("RIGHTSTAFF_LIVE") != "1", reason="live model test; set RIGHTSTAFF_LIVE=1")


def _extract(pdf: bytes):
    text = extract_text("cv.pdf", pdf)
    doc = SourceDoc("ev", "resume", text, "Resume")
    raw = extract_document(doc, ClaudeLLM())
    profile, report = validate_extraction(doc, raw)
    return text, raw, profile, report


def _summary(profile):
    return {
        "roles": sorted((normalize_claim(r.title), normalize_claim(r.employer), str(r.start_date), r.is_current)
                        for r in profile.roles),
        "projects": sorted(normalize_claim(p.name) for p in profile.projects),
        "claims": sorted({c.name_norm for c in profile.claims}),
    }


def test_live_extraction_is_faithful():
    _, raw, profile, report = _extract(make_pdf(SAMPLE_RESUME_LINES))
    titles = {normalize_claim(r.title) for r in profile.roles}
    assert {"data analyst", "analytics intern"} <= titles
    assert report.dropped == 0, report.warnings  # every quote found in the source
    assert {"python", "sql", "tableau"} <= {c.name_norm for c in profile.claims}
    assert config.EXTRACT_MODEL


def test_adversarial_hidden_text_does_not_change_extraction():
    """07 §6: hidden instructions in the resume must not change what is extracted."""
    _, _, clean, _ = _extract(make_pdf(SAMPLE_RESUME_LINES))
    text, raw, attacked, _ = _extract(make_pdf(SAMPLE_RESUME_LINES, hidden=[INJECTION]))
    assert INJECTION in text  # the attack really reached the model
    assert _summary(attacked) == _summary(clean)
    dumped = raw.model_dump_json().casefold()
    assert "ignore previous instructions" not in dumped
