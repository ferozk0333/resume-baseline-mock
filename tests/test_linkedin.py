"""LinkedIn profile link in the recruiter view: entered on the form, or found in the candidate's documents."""
import pytest

from services import linkedin as li
from core import db
from tests.test_api import DS, api, onboard  # noqa: F401  (fixture)


@pytest.mark.parametrize("raw", ["linkedin.com/in/jane-doe", "https://www.linkedin.com/in/jane-doe/",
                                 "http://uk.linkedin.com/in/jane-doe", "www.linkedin.com/in/jane-doe"])
def test_profile_urls_are_normalized(raw):
    assert li.normalize(raw) == "https://www.linkedin.com/in/jane-doe"


@pytest.mark.parametrize("raw", ["https://www.linkedin.com/company/acme", "https://evil.com/in/jane",
                                 "jane-doe", "https://linkedin.com/in/x"])
def test_non_profile_urls_are_rejected(raw):
    with pytest.raises(ValueError):
        li.normalize(raw)


def test_link_is_found_in_documents_pdf_first_and_the_form_wins():
    pdf = {"type": "linkedin_pdf", "raw_text": "Contact\nwww.linkedin.com/in/from-pdf\n(LinkedIn)"}
    resume = {"type": "resume", "raw_text": "Jane | LinkedIn: linkedin.com/in/from-resume | GitHub"}
    readme = {"type": "github_repo", "raw_text": "https://www.linkedin.com/in/from-readme"}
    assert li.profile_link({}, [resume, pdf, readme]) == {"url": "https://www.linkedin.com/in/from-pdf",
                                                          "source": "from the LinkedIn PDF"}
    assert li.profile_link({}, [resume])["source"] == "from the resume"
    assert li.profile_link({}, [readme]) is None                          # only the candidate's own documents
    assert li.profile_link({"linkedin_url": "https://www.linkedin.com/in/typed"}, [pdf])["source"] == \
        "entered by the candidate"


def test_upload_form_saves_the_link_and_rejects_a_bad_one(api):  # noqa: F811
    cid, task = onboard(api, linkedin_url="linkedin.com/in/jane-doe/")
    conn = db.connect(api.app.state.rs.db_path)
    assert conn.execute("SELECT linkedin_url FROM candidates WHERE id = ?", (cid,)).fetchone()[0] == \
        "https://www.linkedin.com/in/jane-doe"
    from tests.helpers import make_pdf
    cid2 = api.post("/candidates", json={"name": "B"}).json()["id"]
    r = api.post(f"/candidates/{cid2}/onboard", data={"baseline_id": DS, "linkedin_url": "https://acme.com/jane"},
                 files={"resume": ("cv.pdf", make_pdf(["x"]), "application/pdf")})
    assert r.status_code == 422 and "LinkedIn profile URL" in r.text
