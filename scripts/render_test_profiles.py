"""Render the synthetic test profiles to resume / LinkedIn files (rightstaff-test-profiles/08 §2).

    python scripts/render_test_profiles.py [--src rightstaff-test-profiles/profiles] [--out DIR]

- Resume → PDF for IDs 1, 3, 5 and DS4_padded; two-column DOCX for IDs 2 and 4.
- IDs 2 and 4 also get a two-column PDF (<ID>_resume_2col.pdf) to test layout-aware parsing.
- LinkedIn section → PDF laid out like a LinkedIn "Save to PDF" export. Skipped when absent.
Output defaults to tests/fixtures/profiles/rendered/ (gitignored; this script regenerates it).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ABSENT = "None provided."


# --- Profile files -----------------------------------------------------------------------------

def parse_profile(path: Path) -> dict:
    """Header fields plus one entry per `## Section` (None when the section says "None provided.")."""
    text = path.read_text()
    head, _, body = text.partition("\n## ")
    out: dict = {"id": path.stem, "sections": {}}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    for chunk in ("## " + body).split("\n## ")[0:]:
        chunk = chunk.removeprefix("## ")
        title, _, content = chunk.partition("\n")
        content = content.strip()
        out["sections"][title.strip()] = None if content == ABSENT else content
    return out


def profile_number(pid: str) -> int:
    m = re.search(r"(\d+)", pid)
    return int(m.group(1)) if m else 0


def blocks(md: str) -> list[tuple[str, str]]:
    """Resume markdown → (kind, text): kind is heading | bullet | line | blank."""
    out = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append(("blank", ""))
        elif line.lstrip().startswith("- "):
            out.append(("bullet", line.lstrip()[2:]))
        elif re.fullmatch(r"\*\*[^*]+\*\*", line.strip()):
            out.append(("heading", line.strip().strip("*")))
        else:
            out.append(("line", line))
    return out


def split_sections(md: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Group resume blocks under their headings (first heading = the name)."""
    sections: list[tuple[str, list]] = [("", [])]
    for kind, text in blocks(md):
        if kind == "heading" and not sections[-1][1] and not sections[-1][0]:
            sections[-1] = (text, [])
        elif kind == "heading" and text.lower() in SECTION_NAMES:
            sections.append((text, []))
        else:
            sections[-1][1].append((kind, text))
    return sections


SECTION_NAMES = {"summary", "experience", "work experience", "education", "skills", "certifications", "projects",
                 "awards", "publications", "volunteering", "leadership", "research", "references", "activities",
                 "profile", "technical skills", "selected projects", "honors"}
SIDEBAR = {"education", "skills", "technical skills", "certifications", "awards", "honors"}


# --- PDF (reportlab) ---------------------------------------------------------------------------

def _rl_inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def _styles():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle("name", parent=base["Title"], fontSize=18, spaceAfter=4, alignment=0),
        "h": ParagraphStyle("h", parent=base["Heading3"], fontSize=11.5, spaceBefore=8, spaceAfter=3),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontSize=9.5, leading=12, spaceAfter=2),
        "b": ParagraphStyle("b", parent=base["BodyText"], fontSize=9.5, leading=12, leftIndent=12,
                            bulletIndent=2, spaceAfter=1),
    }


def _flow(items: list[tuple[str, str]], st) -> list:
    from reportlab.platypus import Paragraph, Spacer
    out = []
    for kind, text in items:
        if kind == "bullet":
            out.append(Paragraph(_rl_inline(text), st["b"], bulletText="•"))
        elif kind == "heading":
            out.append(Paragraph(f"<b>{_rl_inline(text)}</b>", st["p"]))
        elif kind == "line":
            out.append(Paragraph(_rl_inline(text), st["p"]))
        else:
            out.append(Spacer(1, 3))
    return out


def resume_pdf(md: str, path: Path) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    st = _styles()
    story = []
    for title, items in split_sections(md):
        story.append(Paragraph(_rl_inline(title), st["name"] if not story else st["h"]))
        story += _flow(items, st)
    SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=54, rightMargin=54, topMargin=48,
                      bottomMargin=48).build(story)


def resume_pdf_two_column(md: str, path: Path) -> None:
    """Sidebar (education, skills, certifications) on the left, experience on the right."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import BaseDocTemplate, Frame, FrameBreak, PageTemplate, Paragraph
    st = _styles()
    sections = split_sections(md)
    name, top = sections[0]
    side = [s for s in sections[1:] if s[0].lower() in SIDEBAR]
    main = [s for s in sections[1:] if s[0].lower() not in SIDEBAR]
    w, h = LETTER
    margin, gap, side_w, head_h = 40, 18, 170, 70
    frames = [Frame(margin, h - margin - head_h, w - 2 * margin, head_h, id="head"),
              Frame(margin, margin, side_w, h - 2 * margin - head_h, id="side"),
              Frame(margin + side_w + gap, margin, w - 2 * margin - side_w - gap, h - 2 * margin - head_h,
                    id="main")]
    story = [Paragraph(_rl_inline(name), st["name"]), *_flow(top, st), FrameBreak()]
    for title, items in side:
        story += [Paragraph(_rl_inline(title), st["h"]), *_flow(items, st)]
    story.append(FrameBreak())
    for title, items in main:
        story += [Paragraph(_rl_inline(title), st["h"]), *_flow(items, st)]
    doc = BaseDocTemplate(str(path), pagesize=LETTER)
    doc.addPageTemplates([PageTemplate(id="two", frames=frames)])
    doc.build(story)


def linkedin_pdf(md: str, path: Path) -> None:
    """Name, headline and location on top; Experience, Education, Skills, Licenses below."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    st = _styles()
    lines = md.splitlines()
    story, i = [], 0
    header = []
    while i < len(lines) and lines[i].strip():
        header.append(lines[i].strip())
        i += 1
    if header:
        story.append(Paragraph(_rl_inline(header[0]), st["name"]))
        story += [Paragraph(_rl_inline(x), st["p"]) for x in header[1:]]
    known = {"experience", "education", "skills", "licenses & certifications", "licenses", "certifications",
             "projects", "honors & awards", "publications", "volunteering", "about", "recommendations"}
    for raw in lines[i:]:
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 4))
        elif line.lower() in known:
            story.append(Paragraph(_rl_inline(line), st["h"]))
        elif line.startswith("- "):
            entry = line[2:]
            first, *rest = [p.strip() for p in entry.split(" · ")]
            story.append(Paragraph(f"<b>{_rl_inline(first)}</b>", st["p"]))
            story += [Paragraph(_rl_inline(r), st["p"]) for r in rest]
        elif ":" in line and line.split(":", 1)[0].lower() in known:
            k, v = line.split(":", 1)
            story += [Paragraph(_rl_inline(k), st["h"]), Paragraph(_rl_inline(v.strip()), st["p"])]
        else:
            story.append(Paragraph(_rl_inline(line), st["p"]))
    SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=54, rightMargin=54).build(story)


# --- DOCX (python-docx) ------------------------------------------------------------------------

def _docx_runs(par, text: str) -> None:
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            par.add_run(part).bold = i % 2 == 1


def resume_docx_two_column(md: str, path: Path) -> None:
    """Two newspaper columns: sidebar sections fill column 1, experience fills column 2."""
    import docx
    from docx.enum.text import WD_BREAK
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    d = docx.Document()
    sections = split_sections(md)
    name, top = sections[0]
    d.add_heading(name, level=0)
    for kind, text in top:
        if kind != "blank":
            _docx_runs(d.add_paragraph(), text)
    body = d.add_section(0)                       # continuous section break, then two columns
    cols = body._sectPr.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols")
        body._sectPr.append(cols)
    cols.set(qn("w:num"), "2")
    cols.set(qn("w:space"), "360")
    side = [s for s in sections[1:] if s[0].lower() in SIDEBAR]
    main = [s for s in sections[1:] if s[0].lower() not in SIDEBAR]

    def write(group):
        for title, items in group:
            d.add_heading(title, level=2)
            for kind, text in items:
                if kind == "bullet":
                    _docx_runs(d.add_paragraph(style="List Bullet"), text)
                elif kind in ("line", "heading"):
                    p = d.add_paragraph()
                    _docx_runs(p, f"**{text}**" if kind == "heading" else text)

    write(side)
    d.add_paragraph().add_run().add_break(WD_BREAK.COLUMN)
    write(main)
    d.save(str(path))


# --- Main ------------------------------------------------------------------------------------------

def render(src: Path, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for f in sorted(src.glob("*/*.md")):
        prof = parse_profile(f)
        pid, n = prof["id"], profile_number(prof["id"])
        resume, linkedin = prof["sections"].get("Resume"), prof["sections"].get("LinkedIn")
        if resume:
            if n in (2, 4) and "padded" not in pid:
                written.append(out / f"{pid}_resume.docx")
                resume_docx_two_column(resume, written[-1])
                written.append(out / f"{pid}_resume_2col.pdf")
                resume_pdf_two_column(resume, written[-1])
            else:
                written.append(out / f"{pid}_resume.pdf")
                resume_pdf(resume, written[-1])
        if linkedin:
            written.append(out / f"{pid}_linkedin.pdf")
            linkedin_pdf(linkedin, written[-1])
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "rightstaff-test-profiles" / "profiles"))
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "profiles" / "rendered"))
    args = ap.parse_args()
    files = render(Path(args.src), Path(args.out))
    print(f"wrote {len(files)} files to {args.out}")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
