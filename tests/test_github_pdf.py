"""GitHub PDF and Portfolio PDF uploads (mock candidates): the GitHub profile page printed from a browser is read in
code (cards side by side, grouped by position) and its repos follow the API path; the portfolio PDF's text goes
through portfolio.page_from_text. PDFs are drawn here; no real candidate files. Offline."""
import io

import pytest
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from core import config, db
from extraction import store
from extraction.onboard import OnboardingInputs, Upload, ingest
from ingest.github import Repo
from ingest.github_pdf import parse_github_pdf
from tests.helpers import SAMPLE_RESUME_LINES, make_pdf

LEFT, RIGHT = 200, 410


def profile_pdf(heading: str = "Pinned", footer: str | None = "https://github.com/jdoe", extra_card=None) -> bytes:
    """A GitHub profile page as a browser prints it: sidebar on the left, profile README, two cards side by side, a
    profile repo card, the contribution graph and activity below, the page address in the footer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)

    def text(x, y, s, bold=False, size=10):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, s)

    text(40, 740, "Jordan Doe", bold=True, size=16)              # sidebar
    text(40, 720, "jdoe")
    text(40, 600, "Followers 10")
    text(LEFT, 700, "Hi there, I build data tools in Python 3")   # profile README: never read
    text(LEFT, 660, heading, bold=True, size=12)
    text(LEFT, 640, "churn-model", bold=True)                     # left card
    text(LEFT + 80, 640, "Public")
    text(LEFT, 626, "Predicts customer churn with gradient")
    text(LEFT, 614, "boosted trees.")
    text(LEFT, 598, "Python 12 3")
    text(RIGHT, 640, "vizkit", bold=True)                         # right card: a fork with a fork count
    text(RIGHT + 50, 640, "Public")
    text(RIGHT, 626, "Forked from plotco/vizkit")
    text(RIGHT, 614, "Charts for notebooks")
    text(RIGHT, 598, "JavaScript 1.2k 40")
    text(LEFT, 560, "jdoe.github.io", bold=True)                  # the profile site repo: skipped
    text(LEFT + 70, 560, "Public")
    text(LEFT, 546, "My site")
    text(LEFT, 532, "HTML")
    if extra_card:
        for i, (s, bold) in enumerate(extra_card):
            text(RIGHT, 560 - 14 * i, s, bold=bold)
    text(LEFT, 480, "1,234 contributions in the last year")
    text(LEFT, 440, "Contribution activity", bold=True)
    text(LEFT, 420, "ignored-repo", bold=True)
    text(LEFT, 406, "Created 3 commits in jdoe/ignored-repo")
    if footer:
        text(40, 20, footer, size=8)
    text(560, 20, "1/1", size=8)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_cards_side_by_side_are_read_with_fork_from_the_card_and_counts_after_the_language():
    user, repos, warnings = parse_github_pdf(profile_pdf())
    assert user == "jdoe" and warnings == []
    churn, viz = repos
    assert len(repos) == 2                                        # the profile repo is skipped; activity never read
    assert (churn.full_name, churn.html_url) == ("jdoe/churn-model", "https://github.com/jdoe/churn-model")
    assert churn.description == "Predicts customer churn with gradient boosted trees."    # wrapped over two lines
    assert (churn.language, churn.languages, churn.stars, churn.is_fork) == ("Python", {"Python": 0}, 12, False)
    # "3" and "40" are fork counts, not fork status: only "Forked from" marks a fork.
    assert (viz.name, viz.description, viz.language, viz.stars, viz.is_fork) == \
        ("vizkit", "Charts for notebooks", "JavaScript", 1200, True)
    for r in repos:                                               # never invented
        assert r.own_commits == 0 and r.readme is None and r.pushed_at is None and r.created_at is None
    assert churn.counted and not viz.counted                     # the API path's fork rule (0 own commits)


def test_popular_repositories_heading_works_the_same():
    user, repos, _ = parse_github_pdf(profile_pdf(heading="Popular repositories"))
    assert user == "jdoe" and [r.name for r in repos] == ["churn-model", "vizkit"]


def test_text_without_a_visibility_badge_never_starts_a_card():
    _, repos, warnings = parse_github_pdf(profile_pdf(extra_card=[("not-a-card", True), ("Some text", False)]))
    assert [r.name for r in repos] == ["churn-model", "vizkit"]
    assert warnings == ["GitHub PDF: skipped text outside any repository card: 'not-a-card'."]


def test_the_same_repo_twice_is_reported_not_merged():
    _, repos, warnings = parse_github_pdf(profile_pdf(extra_card=[("vizkit Public", True), ("Second copy", False)]))
    assert [r.name for r in repos] == ["churn-model", "vizkit"]
    assert repos[1].description == "Charts for notebooks"       # the first one is kept as it was
    assert warnings == ["GitHub PDF: jdoe/vizkit appears more than once; kept the first."]


# AIE-01 (mock candidate): Chrome's print of a profile with six pinned cards. Each card footer is printed as separate
# runs on one row ("Python" "143" "11"), a little below the description: it used to start a fake card "Python" with
# description "143". Positions as (x, top, size, bold, text), taken from that print; no candidate file is committed.
AIE01 = [
    (36, 237, 11.2, True, "Sushmita Adhikari"), (36, 293, 7.4, False, "AI engineer @ Corvana Health. LLM"),
    (45, 334, 6.5, False, "64 followers \u00b7 41 following"),
    (196, 114, 9.3, True, "Hi, I'm Sushmita"),
    (196, 138, 7.4, False, "AI engineer in Dallas. At work I build LLM features for health plan members."),
    (185, 272.5, 7.4, False, "Pinned"),
    (203.7, 295.2, 6.5, True, "ragcheck"), (241.6, 296, 5.6, False, "Public"),
    (192.6, 308.6, 5.6, False, "pytest plugin for regression-testing RAG pipelines. YAML test cases,"),
    (192.6, 317, 5.6, False, "pluggable graders (exact, embedding, LLM judge), JUnit output for CI."),
    (200, 330.9, 5.6, False, "Python"), (234.1, 330.9, 5.6, False, "143"), (260.2, 330.9, 5.6, False, "11"),
    (403.2, 295.2, 6.5, True, "nepali-news-classifier"), (480.2, 296, 5.6, False, "Public"),
    (392.1, 308.6, 5.6, False, "Fine-tuned XLM-RoBERTa for 16-class Nepali news classification."),
    (392.1, 317, 5.6, False, "Cleaned 70K-article dataset is on Hugging Face."),
    (399.5, 330.9, 5.6, False, "Jupyter Notebook"), (460, 330.9, 5.6, False, "52"), (482.9, 330.9, 5.6, False, "14"),
    (203.7, 362.6, 6.5, True, "sbc-assistant-demo"), (274.2, 363.5, 5.6, False, "Public"),
    (192.6, 376, 5.6, False, "Demo Q&A bot over public sample Summary of Benefits and Coverage"),
    (192.6, 384.4, 5.6, False, "(SBC) PDFs. Section-aware chunking, hybrid search, citations."),
    (200, 398.3, 5.6, False, "Python"), (234.1, 398.3, 5.6, False, "38"), (257.1, 398.3, 5.6, False, "6"),
    (403.2, 362.6, 6.5, True, "llm-cost-meter"), (457.4, 363.5, 5.6, False, "Public"),
    (392.1, 376, 5.6, False, "FastAPI middleware that logs token usage and $ cost per route for"),
    (392.1, 384.4, 5.6, False, "OpenAI and Anthropic SDK calls."),
    (399.5, 398.3, 5.6, False, "Python"), (433.6, 398.3, 5.6, False, "21"), (456.6, 398.3, 5.6, False, "2"),
    (203.7, 430.1, 6.5, True, "hybrid-search-bench"), (277.4, 430.9, 5.6, False, "Public"),
    (192.6, 443.4, 5.6, False, "Notebook: BM25 vs pgvector vs hybrid (RRF) on a few BEIR subsets."),
    (192.6, 451.8, 5.6, False, "Numbers + notes, nothing fancy."),
    (200, 465.8, 5.6, False, "Jupyter Notebook"), (260.5, 465.8, 5.6, False, "17"), (283.5, 465.8, 5.6, False, "1"),
    (403.2, 430.1, 6.5, True, "dotfiles"), (435.7, 430.9, 5.6, False, "Public"),
    (392.1, 443.4, 5.6, False, "zsh, tmux, nvim. Mostly stolen from people smarter than me."),
    (399.5, 465.8, 5.6, False, "Shell"), (428.7, 465.8, 5.6, False, "3"),
    (185, 493.4, 7.4, False, "659 contributions in the last year"), (521, 493.1, 5.6, False, "Contribution settings"),
    (185, 665.4, 7.4, False, "Contribution activity"),
    (203.7, 716.5, 6.5, False, "sushadhikari/ragcheck"), (330.2, 717.3, 5.6, False, "26 commits"),
    (30, 14.8, 6, False, "sushadhikari (Sushmita Adhikari) \u00b7 GitHub"),
    (30, 770, 6, False, "https://github.com/sushadhikari"), (573, 770, 6, False, "1/1"),
]


def aie01_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    for x, top, size, bold, s in AIE01:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, LETTER[1] - top - 0.8 * size, s)          # reportlab measures from the bottom, to the baseline
    c.showPage()
    c.save()
    return buf.getvalue()


def test_aie01_card_footers_set_the_language_and_are_never_cards():
    user, repos, warnings = parse_github_pdf(aie01_pdf())
    assert (user, warnings) == ("sushadhikari", [])
    assert [(r.name, r.language) for r in repos] == [
        ("ragcheck", "Python"), ("nepali-news-classifier", "Jupyter Notebook"),
        ("sbc-assistant-demo", "Python"), ("llm-cost-meter", "Python"),
        ("hybrid-search-bench", "Jupyter Notebook"), ("dotfiles", "Shell")]
    by = {r.name: r for r in repos}
    assert [by[n].stars for n in by] == [143, 52, 38, 21, 17, 3]                     # forks are counts, not status
    assert not any(r.is_fork for r in repos)
    assert by["ragcheck"].description == ("pytest plugin for regression-testing RAG pipelines. YAML test cases, "
                                          "pluggable graders (exact, embedding, LLM judge), JUnit output for CI.")
    assert by["dotfiles"].description == "zsh, tmux, nvim. Mostly stolen from people smarter than me."


def test_without_the_profile_address_nothing_is_read():
    user, repos, warnings = parse_github_pdf(profile_pdf(footer=None))
    assert (user, repos) == (None, [])
    assert "no github.com profile address" in warnings[0]


def test_not_a_pdf_is_one_warning():
    assert parse_github_pdf(b"not a pdf") == (None, [], ["GitHub PDF: the file could not be read as a PDF."])


def test_repo_text_blocks_are_accepted_and_their_readme_dropped():
    a = Repo("etl-kit", "jdoe/etl-kit", "Small ETL helpers", "Python", "https://github.com/jdoe/etl-kit", False,
             None, "2025-03-02T10:00:00Z", {"Python": 900, "Shell": 100}, own_commits=14,
             readme="ETL kit\nLoads CSV files.")
    b = Repo("jdoe", "jdoe/jdoe", "Profile", None, "https://github.com/jdoe/jdoe", False, None, None)
    lines = (a.as_text() + "\n\n" + b.as_text()).splitlines()
    user, repos, warnings = parse_github_pdf(make_pdf(lines))
    assert (user, warnings) == ("jdoe", [])
    (r,) = repos                                                  # the profile README repo is skipped
    assert (r.full_name, r.description, r.language, r.languages) == \
        ("jdoe/etl-kit", "Small ETL helpers", "Python", {"Python": 0, "Shell": 0})
    assert (r.own_commits, r.pushed_at, r.is_fork, r.readme) == (14, "2025-03-02T10:00:00Z", False, None)


# --- ingest() ---------------------------------------------------------------------------------------------------------

class NoNetwork:
    """A GitHub client / HTTP session that fails the test if it is used."""
    def fetch_repos(self, *a, **k):
        raise AssertionError("the GitHub API must not be called when a GitHub PDF is uploaded")

    def get(self, *a, **k):
        raise AssertionError("the portfolio URL must not be fetched when a portfolio PDF is uploaded")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up")
    return db.init_app_db(str(tmp_path / "g.db"))


def run(conn, **kw):
    inputs = OnboardingInputs(resume=Upload("cv.pdf", make_pdf(SAMPLE_RESUME_LINES)), name="Jordan Doe", **kw)
    return ingest(conn, inputs, github_client=NoNetwork(), portfolio_session=NoNetwork())


def test_github_pdf_is_used_instead_of_the_field_and_follows_the_api_path(conn):
    cid, docs, warnings = run(conn, github_pdf=Upload("gh.pdf", profile_pdf()))
    assert warnings == []
    repos = [d for d in docs if d.type == "github_repo"]
    assert [d.repo.name for d in repos] == ["churn-model", "vizkit"]
    ev = [e for e in store.list_evidence(conn, cid) if e["type"] == "github_repo"]
    assert len(ev) == 2 and all("Commits by user: 0" in e["raw_text"] for e in ev)
    assert store.get_candidate(conn, cid)["github_username"] == "jdoe"      # the field was empty


def test_github_field_username_is_kept_but_not_read_when_a_pdf_is_uploaded(conn):
    cid, docs, _ = run(conn, github="someone-else", github_pdf=Upload("gh.pdf", profile_pdf()))
    assert [d.repo.full_name for d in docs if d.type == "github_repo"] == ["jdoe/churn-model", "jdoe/vizkit"]
    assert store.get_candidate(conn, cid)["github_username"] == "someone-else"


def test_portfolio_pdf_text_goes_through_page_from_text_and_the_url_is_not_fetched(conn):
    lines = ["Jordan Doe portfolio", "Churn model case study: predicting churn for a subscription business.",
             "Dashboard redesign: a Tableau dashboard used by the sales team every week."] * 3
    cid, docs, warnings = run(conn, portfolio_url="https://example.com",
                              portfolio_pdf=Upload("site.pdf", make_pdf(lines)))
    assert warnings == []
    (doc,) = [d for d in docs if d.type == "portfolio"]
    assert "Churn model case study" in doc.text
    (ev,) = [e for e in store.list_evidence(conn, cid) if e["type"] == "portfolio"]
    assert ev["source"] == "upload" and ev["raw_text"] == doc.text
