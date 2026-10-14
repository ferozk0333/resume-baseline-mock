"""Demo data: structured test-profile files → saved profiles, without any model (phase 11).

The files in rightstaff-test-profiles/profiles/<role>/<ID>.md are written in a fixed format, so code can
read them exactly: every role, bullet, project, claim and certification keeps the exact line it came
from as its quote, and each section becomes its own evidence source (resume, LinkedIn, GitHub repos,
portfolio, form entries). The sources then go through the same cross-source merge and save as a real
onboarding. Candidates made this way are marked data_source = "demo_parsed" and labelled in the UI.

DESIGN.md is never read: it describes the intended order and must not leak into the data.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from core.db import now_iso
from core.text import contains_phrase
from extraction import store
from extraction.onboard import CertificationForm, OnboardingInputs, ReferenceForm, finish_onboarding, form_profile
from extraction.records import BulletRec, CertificationRec, ClaimRec, Profile, ProjectRec, RoleRec
from extraction.validate import SourceDoc, ValidationReport, normalize_claim, parse_month, repo_profile
from ingest.github import Repo, is_profile_repo

ABSENT = "None provided."
DEMO_SOURCE = "demo_parsed"
ROLE_FOLDERS = {"data-scientist": "Data Scientist", "ai-engineer": "AI Engineer", "product-manager": "Product Manager"}

EXPERIENCE = {"experience", "professional experience", "work experience"}
PROJECTS = {"projects", "selected projects"}
PUBLICATIONS = {"publications"}
SKILLS = {"skills", "technical skills", "core competencies"}
CERTS = {"certifications", "licenses & certifications"}
OTHER = {"summary", "objective", "professional summary", "education", "interests",
         "education & professional development", "profile"}

# Names treated as tools (languages, libraries, frameworks, platforms, products); everything else is a skill.
# Kind does not change any score (phase 9 review); this keeps the demo data readable.
TOOLS = {
    "python", "r", "sql", "java", "scala", "c++", "javascript", "typescript", "go", "bash", "excel", "tableau",
    "power bi", "looker", "pandas", "numpy", "scikit-learn", "sklearn", "pytorch", "tensorflow", "keras", "spark",
    "hadoop", "hive", "kafka", "airflow", "aws", "azure", "gcp", "google cloud", "snowflake", "bigquery", "docker",
    "kubernetes", "git", "github", "fastapi", "flask", "django", "react", "postgresql", "postgresql/pgvector",
    "pgvector", "mysql", "mongodb", "hugging face", "langgraph", "langchain", "llamaindex", "statsmodels",
    "lightgbm", "xgboost", "matplotlib", "seaborn", "plotly", "dbt", "jira", "figma", "amplitude", "mixpanel",
    "google analytics", "notion", "confluence", "sagemaker", "databricks", "mlflow", "streamlit", "openai api",
    "llm apis", "rest apis", "ci/cd", "linux", "redis", "elasticsearch", "opensearch", "chroma", "qdrant",
}


@dataclass
class ProfileFile:
    id: str
    role: str
    name: str | None
    github_username: str | None
    sections: dict[str, str | None]


def read_profile_file(path: Path) -> ProfileFile:
    text = path.read_text()
    head, _, body = text.partition("\n## ")
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    sections: dict[str, str | None] = {}
    for chunk in ("## " + body).split("\n## "):
        title, _, content = chunk.removeprefix("## ").partition("\n")
        content = content.strip()
        sections[title.strip()] = None if content == ABSENT else content
    gh = meta.get("github-username")
    return ProfileFile(path.stem, ROLE_FOLDERS.get(path.parent.name, path.parent.name), meta.get("name"),
                       None if gh in (None, "", "none") else gh, sections)


def plain(line: str) -> str:
    """The line as it appears in the rendered document (bold markers removed)."""
    return line.replace("**", "").strip()


def source_text(markdown: str) -> str:
    return "\n".join(plain(line) for line in markdown.splitlines())


_DATES = re.compile(r"(?P<start>[A-Z][a-z]{2,8}\.? \d{4}|\d{4})\s*[–-]\s*(?P<end>[A-Z][a-z]{2,8}\.? \d{4}|\d{4}|Present|Current)")


def dates(text: str) -> tuple[date | None, date | None, bool]:
    m = _DATES.search(text)
    if not m:
        return None, None, False
    start = parse_month(_month_text(m["start"]), is_end=False).value
    current = m["end"] in ("Present", "Current")
    end = None if current else parse_month(_month_text(m["end"]), is_end=True).value
    return start, end, current


def _month_text(s: str) -> str:
    """'Mar 2024' → '2024-03'; '2022' stays a year."""
    s = s.replace(".", "")
    for fmt in ("%b %Y", "%B %Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    return s


def _kind(name: str) -> str:
    base = re.sub(r"\s*\(.*?\)\s*", "", name).strip().casefold()
    return "tool" if base in TOOLS else "skill"


def _claims(line: str, evidence_id: str) -> list[ClaimRec]:
    names = [n.strip().rstrip(".") for n in re.split(r"[,·]", line) if n.strip()]
    return [ClaimRec(kind=_kind(n), name_raw=n, name_norm=normalize_claim(n), quote=n, evidence_id=evidence_id)
            for n in names if len(n) <= 60]


def _certification(line: str, evidence_id: str) -> CertificationRec:
    parts = [p.strip() for p in line.split(" · ")]
    name = re.sub(r",\s*\d{4}$", "", parts[0]).strip()
    year = next((p for p in parts[1:] if re.fullmatch(r"(Issued )?\d{4}", p)), None)
    if year is None:
        m = re.search(r"\b(19|20)\d{2}\b", parts[0])
        year = m.group(0) if m else None
    cred = next((p.split("Credential ID", 1)[1].strip() for p in parts if "Credential ID" in p), None)
    return CertificationRec(name=name, issuer=None,
                            issued_date=parse_month(year.replace("Issued ", ""), is_end=False).value if year else None,
                            credential_id=cred, verify_url=None, evidence_id=evidence_id)


# --- Sources ----------------------------------------------------------------------------------------

def resume_profile(markdown: str, evidence_id: str) -> Profile:
    profile = Profile()
    section, current = None, None
    first_bold = True
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.fullmatch(r"\*\*([^*]+)\*\*", line)
        if heading:
            name = heading.group(1).strip().casefold()
            if first_bold and name not in EXPERIENCE | PROJECTS | SKILLS | CERTS | OTHER | PUBLICATIONS:
                first_bold = False                         # the candidate's name
                continue
            first_bold = False
            if name in EXPERIENCE | PROJECTS | PUBLICATIONS | SKILLS | CERTS | OTHER:
                section, current = name, None
                continue
        if line.startswith("- ") and current is not None:
            current.bullets.append(BulletRec(text=line[2:].strip(), location=None, evidence_id=evidence_id))
            continue
        if section in EXPERIENCE and (m := re.match(r"\*\*(?P<title>.+?)\*\*\s+[—-]\s+(?P<rest>.+)$", line)):
            org = m["rest"].split(" · ")[0]
            start, end, is_current = dates(m["rest"])
            current = RoleRec(title=m["title"].strip(), employer=org.split(",")[0].strip(), industry=None,
                              start_date=start, end_date=end, is_current=is_current, team_size=None,
                              quote=plain(line), evidence_id=evidence_id)
            profile.roles.append(current)
        elif section in PROJECTS and (m := re.match(r"\*\*(?P<name>.+?)\*\*(?P<rest>.*)$", line)):
            url = re.search(r"\(((?:https?://)?[\w.-]+\.[a-z]{2,}/[^)\s]*)\)", m["rest"])
            start, end, _ = dates(m["rest"])
            current = ProjectRec(kind="project", name=m["name"].strip(), description=None,
                                 url=("https://" + url.group(1).removeprefix("https://")) if url else None,
                                 start_date=start, end_date=end, quote=plain(line), evidence_id=evidence_id)
            profile.projects.append(current)
        elif section in PUBLICATIONS and line.startswith("- "):
            text = line[2:].strip()
            profile.projects.append(ProjectRec(kind="publication", name=text.split(" — ")[0].strip(' "“”'),
                                               description=text, url=None, start_date=None, end_date=dates(text)[1],
                                               quote=text, evidence_id=evidence_id))
        elif section in SKILLS:
            profile.claims += _claims(plain(line), evidence_id)
        elif section in CERTS:
            profile.certifications.append(_certification(plain(line), evidence_id))
    _link_claims(profile)
    return profile


def _link_claims(profile: Profile) -> None:
    """A listed skill that a bullet names is shown in that bullet (like extraction's bullet_ref)."""
    bullets = profile.all_bullets()
    for c in profile.claims:
        hit = next((b for b in bullets if contains_phrase(b.text, re.sub(r"\s*\(.*?\)", "", c.name_raw))), None)
        c.bullet_id = hit.id if hit else None


def linkedin_profile(text: str, evidence_id: str) -> Profile:
    profile = Profile()
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        low = line.casefold()
        if low in ("experience", "education", "licenses & certifications", "projects", "honors & awards"):
            section = low
            continue
        if low.startswith("skills:"):
            profile.claims += _claims(line.split(":", 1)[1], evidence_id)
            continue
        if not line.startswith("- "):
            continue
        entry = line[2:]
        if section == "experience":
            parts = [p.strip() for p in entry.split(" · ")]
            title, _, employer = parts[0].partition(",")
            start, end, current = dates(entry)
            role = RoleRec(title=title.strip(), employer=employer.strip() or None, industry=None, start_date=start,
                           end_date=end, is_current=current, team_size=None, quote=line, evidence_id=evidence_id)
            if len(parts) > 2:                                   # the role's description line
                role.bullets.append(BulletRec(text=parts[-1], location=None, evidence_id=evidence_id))
            profile.roles.append(role)
        elif section == "licenses & certifications":
            profile.certifications.append(_certification(entry, evidence_id))
    return profile


def github_repos(data: dict) -> list[Repo]:
    user = data.get("username") or ""
    out = []
    for r in data.get("repos", []):
        if r.get("archived") or is_profile_repo(r["name"], user):
            continue
        langs = r.get("languages") or []
        out.append(Repo(name=r["name"], full_name=f"{user}/{r['name']}", description=r.get("description"),
                        language=langs[0] if langs else None, html_url=f"https://github.com/{user}/{r['name']}",
                        is_fork=bool(r.get("is_fork")), created_at=None,
                        pushed_at=f"{r['last_push']}-01" if r.get("last_push") else None,
                        languages={lang: 0 for lang in langs}, own_commits=int(r.get("own_commits") or 0),
                        readme=r.get("readme") or None))
    return out


def portfolio_profile(text: str, evidence_id: str) -> Profile:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    numbered = [ln for ln in lines if re.match(r"\d+\.\s", ln)]
    projects = []
    for ln in numbered:
        body = re.sub(r"^\d+\.\s*", "", ln)
        name, _, desc = body.partition(":")
        projects.append(ProjectRec(kind="case_study", name=name.strip(' "“”'), description=desc.strip() or None,
                                   url=None, start_date=None, end_date=None, quote=ln, evidence_id=evidence_id))
    if not projects and lines:
        site, _, desc = lines[0].partition(" — ")
        projects.append(ProjectRec(kind="work_sample", name=f"Portfolio: {site.strip()}", description=desc or None,
                                   url=f"https://{site.strip()}", start_date=None, end_date=None, quote=lines[0],
                                   evidence_id=evidence_id))
    return Profile(projects=projects)


# --- Loading --------------------------------------------------------------------------------------

def _evidence(conn, cid: str, kind: str, text: str, profile: Profile | None = None, file_path: str | None = None) -> str:
    eid = store.add_evidence(conn, cid, kind, "demo", raw_text=text, file_path=file_path, status="validated")
    return eid


def _set_extracted(conn, eid: str, profile: Profile) -> None:
    n = len(profile.roles) + len(profile.projects) + len(profile.all_bullets()) + len(profile.claims) \
        + len(profile.certifications)
    with conn:
        conn.execute("UPDATE evidence_items SET extracted_json = ?, items_extracted = ?, items_dropped = 0 WHERE id = ?",
                     (json.dumps(asdict(profile), default=str), n, eid))


def rendered_resume(rendered_dir: Path | None, profile_id: str) -> str | None:
    """The rendered resume file for the viewer: a PDF when one exists (two-column IDs have a PDF too)."""
    if rendered_dir is None:
        return None
    for name in (f"{profile_id}_resume.pdf", f"{profile_id}_resume_2col.pdf", f"{profile_id}_resume.docx"):
        if (rendered_dir / name).exists():
            return str(rendered_dir / name)
    return None


def load_profile_file(conn, path: Path, rendered_dir: Path | None = None) -> str:
    """Create a demo candidate from one structured profile file. Returns the candidate id."""
    pf = read_profile_file(path)
    cid = store.create_candidate(conn, pf.name, pf.role, pf.github_username, None)
    with conn:
        conn.execute("UPDATE candidates SET data_source = ? WHERE id = ?", (DEMO_SOURCE, cid))
    s = pf.sections
    profiles: list[Profile] = []
    if s.get("Resume"):
        eid = _evidence(conn, cid, "resume", source_text(s["Resume"]), file_path=rendered_resume(rendered_dir, pf.id))
        profiles.append(resume_profile(s["Resume"], eid))
        _set_extracted(conn, eid, profiles[-1])
    if s.get("LinkedIn"):
        eid = _evidence(conn, cid, "linkedin_pdf", s["LinkedIn"])
        profiles.append(linkedin_profile(s["LinkedIn"], eid))
        _set_extracted(conn, eid, profiles[-1])
    if s.get("GitHub"):
        data = json.loads(re.sub(r"^```json\s*|\s*```$", "", s["GitHub"].strip()))
        for repo in github_repos(data):
            eid = store.add_evidence(conn, cid, "github_repo", "demo", raw_text=repo.as_text(), status="validated")
            profiles.append(repo_profile(SourceDoc(eid, "github_repo", repo.as_text(), repo.name, repo=repo), None)[0])
    if s.get("Portfolio"):
        eid = _evidence(conn, cid, "portfolio", s["Portfolio"])
        profiles.append(portfolio_profile(s["Portfolio"], eid))
        _set_extracted(conn, eid, profiles[-1])

    def form(key, cls):
        raw = s.get(key)
        return [cls(**x) for x in json.loads(re.sub(r"^```json\s*|\s*```$", "", raw.strip()))] if raw else []
    forms = form_profile(conn, cid, OnboardingInputs(resume=None, certifications=form("Certifications (form)",
                                                                                         CertificationForm),
                                                     references=form("References (form)", ReferenceForm)))
    finish_onboarding(conn, cid, forms, profiles, ValidationReport(), [])
    return cid


def load_all(conn, profiles_dir: Path, rendered_dir: Path | None = None) -> dict[str, str]:
    """profile id (e.g. "DS1") → candidate id, for every structured profile file."""
    return {p.stem: load_profile_file(conn, p, rendered_dir) for p in sorted(profiles_dir.glob("*/*.md"))}


__all__ = ["DEMO_SOURCE", "load_all", "load_profile_file", "read_profile_file", "now_iso"]
