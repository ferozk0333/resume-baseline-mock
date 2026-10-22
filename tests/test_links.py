"""Links are never broken (audit round, item 4). One list of cases runs through core/links.py and, with Node,
through frontend/lib/links.ts, so the backend and every screen read a URL the same way. Offline."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.links import decode_pdf_escapes, find_urls, href_for, markdown_links
from ingest.documents import clean_markdown

ROOT = Path(__file__).resolve().parent.parent
LANCET = "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736(26)01714-9/abstract"

# (text, the URLs found in it, exactly)
PLAIN = [
    (f"Co-Author, The Lancet (Link) ({LANCET}) 2026", [LANCET]),
    (f"Read it at {LANCET}.", [LANCET]),
    ("Badge: https://www.credly.com/badges/8c0a3fb1-e88c-4706-94e5-93991c08a908/linked_in_profile, verified",
     ["https://www.credly.com/badges/8c0a3fb1-e88c-4706-94e5-93991c08a908/linked_in_profile"]),
    ("DOI https://doi.org/10.1016/S0140-6736(20)30183-5; cited", ["https://doi.org/10.1016/S0140-6736(20)30183-5"]),
    ("https://doi.org/10.1002/(SICI)1097-4571(199806)49:8<693::AID-ASI4>3.0.CO;2-0",
     ["https://doi.org/10.1002/(SICI)1097-4571(199806)49:8"]),            # "<" never belongs to a URL in text
    ("https://en.wikipedia.org/wiki/Python_(programming_language)", ["https://en.wikipedia.org/wiki/Python_(programming_language)"]),
    ("(see https://en.wikipedia.org/wiki/Python_(programming_language))",
     ["https://en.wikipedia.org/wiki/Python_(programming_language)"]),
    ("Percent: https://example.org/a%20b/%28x%29?q=%E2%9C%93&lang=en#sec-2!",
     ["https://example.org/a%20b/%28x%29?q=%E2%9C%93&lang=en#sec-2"]),
    ("https://www.linkedin.com/in/jane-doe-12345/?originalSubdomain=uk.", ["https://www.linkedin.com/in/jane-doe-12345/?originalSubdomain=uk"]),
    ("Repo: github.com/x (no scheme) and www.example.org/path, then https://github.com/jdoe-dev/rag-eval-kit#readme",
     ["www.example.org/path", "https://github.com/jdoe-dev/rag-eval-kit#readme"]),
    ("Quoted “https://example.org/q?a=1&b=2”", ["https://example.org/q?a=1&b=2"]),
    ("Array https://example.org/a[0]/b] end", ["https://example.org/a[0]/b"]),
    ("no link here: example dot org", []),
]

MARKDOWN = [
    (f"[The Lancet]({LANCET}) 2026", [("The Lancet", LANCET)]),
    ("[wiki](https://en.wikipedia.org/wiki/Python_(programming_language)), and [b](https://b.org/x?y=1&z=%2F)",
     [("wiki", "https://en.wikipedia.org/wiki/Python_(programming_language)"), ("b", "https://b.org/x?y=1&z=%2F")]),
    ("[not a link] (https://a.org)", []),
]


@pytest.mark.parametrize("text,urls", PLAIN)
def test_plain_text_urls(text, urls):
    assert [u for _, _, u in find_urls(text)] == urls


@pytest.mark.parametrize("text,links", MARKDOWN)
def test_markdown_links(text, links):
    assert [(label, url) for _, _, label, url in markdown_links(text)] == links


def test_pdf_link_escapes_are_decoded_and_valid_escapes_kept():
    broken = "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736%0x2826%0x2901714-9/abstract"
    assert decode_pdf_escapes(broken) == LANCET
    assert decode_pdf_escapes("https://a.org/%28x%29%20y") == "https://a.org/%28x%29%20y"     # real %XX untouched
    md = "Co-Author, The Lancet [Link](" + broken + ") 2026"
    assert clean_markdown(md).strip() == f"Co-Author, The Lancet Link ({LANCET}) 2026"


def test_href_is_the_stored_url_exactly():
    for url in [LANCET, "https://example.org/a%20b/%28x%29?q=1&r=2#s", "mailto:a@example.org", "/api/x"]:
        assert href_for(url) == url
    assert href_for("github.com/jdoe-dev") == "https://github.com/jdoe-dev"


def test_the_resume_parser_keeps_certificate_and_publication_links():
    from extraction.parse.resume import strip_links
    name, url = strip_links("AWS Certified Machine Learning Engineer – Associate (MLA-C01), Amazon Web Services (Link) "
                            "(https://www.credly.com/badges/8c0a3fb1-e88c-4706-94e5-93991c08a908) 2026")
    assert url == "https://www.credly.com/badges/8c0a3fb1-e88c-4706-94e5-93991c08a908"
    assert name == "AWS Certified Machine Learning Engineer – Associate (MLA-C01), Amazon Web Services 2026"
    name, url = strip_links(f"Co-Author, “The Lancet Commission,” The Lancet (Link) ({LANCET}) 2026")
    assert url == LANCET and name == "Co-Author, “The Lancet Commission,” The Lancet 2026"


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_frontend_reads_every_case_the_same_way(tmp_path):
    src = tmp_path / "links.mts"
    src.write_text((ROOT / "frontend" / "lib" / "links.ts").read_text())
    cases = {"plain": [t for t, _ in PLAIN], "md": [t for t, _ in MARKDOWN],
             "href": [LANCET, "github.com/jdoe-dev", "mailto:a@example.org"]}
    script = (f"import('{src.as_posix()}').then(m => console.log(JSON.stringify({{"
              f"plain: {json.dumps(cases['plain'])}.map(t => m.findUrls(t).map(x => x[2])),"
              f"md: {json.dumps(cases['md'])}.map(t => m.markdownLinks(t).map(x => [x[2], x[3]])),"
              f"href: {json.dumps(cases['href'])}.map(m.hrefFor),"
              f"split: m.splitLinks('See [The Lancet]({LANCET}) or https://a.org/x(1).')"
              f"}})))")
    out = json.loads(subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", "-e", script],
                                    capture_output=True, text=True, check=True).stdout)
    assert out["plain"] == [u for _, u in PLAIN]
    assert out["md"] == [[list(x) for x in links] for _, links in MARKDOWN]
    assert out["href"] == [href_for(u) for u in cases["href"]]
    assert out["split"] == [{"text": "See "}, {"text": "The Lancet", "url": LANCET}, {"text": " or "},
                            {"text": "https://a.org/x(1)", "url": "https://a.org/x(1)"}, {"text": "."}]
