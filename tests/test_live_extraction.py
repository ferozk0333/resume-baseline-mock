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


# --- Real extraction of rendered test profiles, checked against their exact parsed version (phase 11) -----
# Provider-neutral: uses whatever EXTRACT_MODEL / LLM_PROVIDER is configured (Claude, Gemini or Groq).

@pytest.mark.parametrize("pid,folder,filename", [
    ("AI1", "ai-engineer", "AI1_resume.pdf"),               # one-column PDF
    ("DS2", "data-scientist", "DS2_resume_2col.pdf"),       # two-column PDF
    ("PM4", "product-manager", "PM4_resume.docx"),          # DOCX
])
def test_rendered_profile_extraction_matches_the_parsed_profile(pid, folder, filename):
    from core.llm import RoutedLLM
    from extraction.structured import read_profile_file, resume_profile
    rendered = config.ROOT / "tests" / "fixtures" / "profiles" / "rendered" / filename
    if not rendered.exists():
        pytest.skip("run scripts/render_test_profiles.py first")
    text = extract_text(filename, rendered.read_bytes())
    doc = SourceDoc("ev", "resume", text, "Resume")
    profile, report = validate_extraction(doc, extract_document(doc, RoutedLLM()))
    expected = resume_profile(read_profile_file(config.ROOT / "rightstaff-test-profiles" / "profiles" / folder /
                                                f"{pid}.md").sections["Resume"], "x")
    assert [r.title for r in profile.roles] == [r.title for r in expected.roles]
    assert len(profile.all_bullets()) >= len(expected.all_bullets()) - 1     # at most one bullet lost
    assert report.dropped <= 2
