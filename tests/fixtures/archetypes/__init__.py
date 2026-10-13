"""Synthetic archetypes (specs/07 §3): DB profiles + scripted FakeJudge labels, scored against Data Scientist.

Each builder saves a profile and returns a FakeJudge whose labels describe what a
careful human would answer for that evidence. Unscripted questions get the
question's none-option. Skills/tools are only judged when the text mentions them
(the same alias pre-filter real runs use), so skills lists and bullets must say
what they claim.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

from extraction import store
from extraction.records import (
    BulletRec, CertificationRec, ClaimRec, Profile, ProjectRec, ReferenceRec, RoleRec,
)
from extraction.validate import normalize_claim
from judges.fake_judge import FakeJudge
from judges.questions import QUESTIONS

TODAY = date(2026, 6, 15)
CRITERION_QUESTIONS = {"skill_evidence", "applied_level", "tool_evidence"}


def ago(months: int) -> date:
    idx = TODAY.year * 12 + TODAY.month - 1 - months
    return date(idx // 12, idx % 12 + 1, 1)


@dataclass
class Builder:
    conn: object
    name: str
    labels: dict = field(default_factory=dict)          # (question, subject_id) → label
    criteria: dict = field(default_factory=dict)        # (question, criterion name) → label
    defaults: dict = field(default_factory=dict)        # question → label
    profile: Profile = field(default_factory=Profile)

    def __post_init__(self):
        self.cid = store.create_candidate(self.conn, self.name)

    # --- items -----------------------------------------------------------------------------
    def bullet(self, text, metric=False, direction=False, impact="none", ownership="not_stated",
               reach="not_stated") -> BulletRec:
        b = BulletRec(text=text, location=None, evidence_id=None)
        self.labels.update({("outcome_metric", b.id): "yes" if metric else "no",
                            ("outcome_direction", b.id): "yes" if direction else "no",
                            ("impact_type", b.id): impact, ("ownership", b.id): ownership, ("reach", b.id): reach})
        return b

    def _item(self, item_id, relevance, complexity, quality, work_sample=None):
        self.labels[("relevance", item_id)] = relevance
        if complexity:
            self.labels[("complexity", item_id)] = complexity
        for i, yes in enumerate(quality, 1):
            self.labels[(f"quality_check_{i}", item_id)] = "yes" if yes else "no"
        if work_sample is not None:
            self.labels[("work_sample_relevant", item_id)] = "yes" if work_sample else "no"

    def role(self, title, employer, start, end=None, current=False, bullets=(), relevance="relevant",
             scope="partly", complexity=None, quality=(1, 0, 1, 0, 0)) -> RoleRec:
        r = RoleRec(title=title, employer=employer, industry=None, start_date=ago(start),
                    end_date=None if current or end is None else ago(end), is_current=current, team_size=None,
                    quote=title, evidence_id=None, bullets=list(bullets))
        self.profile.roles.append(r)
        self._item(r.id, relevance, complexity, quality)
        self.labels[("scope_vs_baseline", r.id)] = scope
        return r

    def project(self, name, kind="project", description=None, bullets=(), end=0, relevance="relevant",
                complexity=None, quality=(1, 0, 0, 1, 0), github=None, work_sample=None) -> ProjectRec:
        p = ProjectRec(kind=kind, name=name, description=description, url=None, start_date=None,
                       end_date=ago(end), quote=name, evidence_id=None, bullets=list(bullets))
        if github is not None:  # (own_commits, languages)
            commits, langs = github
            p.is_fork, p.own_commits = False, commits
            p.evidence_id = store.add_evidence(
                self.conn, self.cid, "github_repo", "api",
                raw_text=f"Repository: x/{name}\nDescription: {description or ''}\nLanguages: {langs}")
        self.profile.projects.append(p)
        self._item(p.id, relevance, complexity, quality, work_sample)
        return p

    def skills_list(self, *names: str, proof="claimed_only"):
        for n in names:
            c = ClaimRec(kind="tool", name_raw=n, name_norm=normalize_claim(n), quote=n, evidence_id=None)
            self.profile.claims.append(c)
            self.labels[("proof_support", c.id)] = proof

    def certification(self, name, credential_id=None):
        self.profile.certifications.append(CertificationRec(name=name, issuer=None, issued_date=None,
                                                            credential_id=credential_id, verify_url=None,
                                                            evidence_id=None))

    def reference(self, relationship, statement, confirms=True):
        r = ReferenceRec(name="Ref", relationship=relationship, contact=None, statement=statement)
        self.profile.references.append(r)
        self.labels[("reference_confirms", r.id)] = "confirms_relevant_work" if confirms else "does_not"

    def skill(self, question, name, label):
        self.criteria[(question, name)] = label

    def github_aligned(self, aligned=True):
        self.labels[("source_alignment", f"github:{self.cid}")] = "aligned" if aligned else "not_aligned"

    # --- finish ----------------------------------------------------------------------------
    def save(self) -> tuple[str, FakeJudge]:
        store.save_profile(self.conn, self.cid, self.profile)
        # The resume's own extraction, used by Consistency as the reference summary.
        eid = store.add_evidence(self.conn, self.cid, "resume", "upload", raw_text="(fixture)", status="validated")
        with self.conn:
            self.conn.execute("UPDATE evidence_items SET extracted_json = ? WHERE id = ?",
                              (json.dumps(asdict(self.profile), default=str), eid))
        labels, criteria, defaults = dict(self.labels), dict(self.criteria), dict(self.defaults)

        def script(q, ctx):
            sid = ctx.get("_subject_id")
            if (q, sid) in labels:
                return labels[(q, sid)]
            if q in CRITERION_QUESTIONS:
                name = ctx["criterion"].split(":", 1)[0]
                if (q, name) in criteria:
                    return criteria[(q, name)]
            return defaults.get(q, QUESTIONS[q].none_option)

        return self.cid, FakeJudge(script=script)


# Skills a Data Scientist baseline looks for, and how strong-evidence profiles show them.
DS_STRONG_SKILLS = {
    "Statistical analysis": ("bullet", "skill_task_result"),
    "Machine learning": ("bullet", "skill_task_result"),
    "Data wrangling": ("bullet", "skill_task"),
    "Exploratory data analysis": ("bullet", "skill_task"),
    "Experimentation / A/B testing": ("bullet", "skill_task_result"),
    "Data visualization and communication": ("bullet", "skill_task"),
}


def strong_ds(conn):
    b = Builder(conn, "Strong early-career DS")
    b.role("Data Scientist", "Acme Retail", start=17, current=True, scope="meets", complexity="meets",
           quality=(1, 1, 1, 1, 1), bullets=[
               b.bullet("Built a churn classification model (machine learning) in Python and scikit-learn that cut "
                        "churn 12%, saving $400K a year.", True, True, "business", "led", "cross_functional"),
               b.bullet("Designed A/B testing for pricing pages with hypothesis testing; lifted conversion 4%.",
                        True, True, "business", "led", "cross_functional"),
               b.bullet("Did data cleaning and EDA across 3 CRM sources with pandas and SQL in BigQuery.",
                        False, False, "operational", "contributed", "team"),
               b.bullet("Built Tableau dashboards for the sales leadership team.", False, False, "operational",
                        "led", "cross_functional"),
           ])
    b.project("churn-model", "repo", "Churn classification with scikit-learn", end=1, complexity="meets",
              github=(48, "Python, Jupyter Notebook"), work_sample=True, quality=(1, 1, 1, 1, 1))
    b.project("ab-test-toolkit", "repo", "A/B testing power calculations in Python", end=2, complexity="partly",
              github=(23, "Python"), work_sample=True, quality=(1, 0, 1, 1, 1))
    b.skills_list("Python", "SQL", "pandas", "scikit-learn", "Tableau", "BigQuery", "Git", proof="artifact")
    b.certification("Google Data Analytics Certificate", credential_id="GDA-123")
    b.github_aligned()
    for name, (ev, applied) in DS_STRONG_SKILLS.items():
        b.skill("skill_evidence", name, ev)
        b.skill("applied_level", name, applied)
    for tool in ("Python", "SQL", "pandas", "scikit-learn", "BI tool", "Cloud data warehouse", "Git"):
        b.skill("tool_evidence", tool, "used")
    return b.save()


def keyword_heavy(conn):
    b = Builder(conn, "Keyword-heavy")
    b.role("Analyst", "Foo Corp", start=17, current=True, relevance="adjacent", scope="below", complexity="no",
           quality=(0, 0, 1, 0, 0), bullets=[
               b.bullet("Worked on various data projects.", ownership="contributed"),
               b.bullet("Supported the team with analysis and reporting.", ownership="contributed"),
           ])
    b.skills_list("Statistical analysis", "Machine learning", "Data wrangling", "Exploratory data analysis",
                  "A/B testing", "Data visualization", "Forecasting", "Causal inference",
                  "Python", "SQL", "pandas", "scikit-learn", "Tableau", "BigQuery", "Git")
    for name in [*DS_STRONG_SKILLS, "Forecasting", "Causal inference"]:
        b.skill("skill_evidence", name, "keyword")
    for tool in ("Python", "SQL", "pandas", "scikit-learn", "BI tool", "Cloud data warehouse", "Git"):
        b.skill("tool_evidence", tool, "listed")
    return b.save()


def project_strong_grad(conn):
    b = Builder(conn, "Project-strong grad")
    b.project("Churn predictor", "repo", "Machine learning churn classification on telco data", end=1,
              complexity="meets", github=(35, "Python"), work_sample=True, quality=(1, 1, 1, 1, 1), bullets=[
                  b.bullet("Trained a gradient boosting classification model in scikit-learn; recall rose to 0.81.",
                           True, True, "none", "led", "own_work")])
    b.project("Store sales forecasting", "repo", "Time series forecasting with pandas", end=3,
              complexity="partly", github=(20, "Python"), work_sample=True, quality=(1, 1, 1, 1, 1), bullets=[
                  b.bullet("Built demand forecasting with pandas; cut forecast error 15% versus the naive baseline.",
                           True, True, "operational", "led", "own_work")])
    b.project("Survey EDA", "project", "Exploratory data analysis and data cleaning of a 10k-response survey",
              end=6, complexity="partly", quality=(1, 1, 1, 1, 1), bullets=[
                  b.bullet("Ran hypothesis testing and regression in Python; presented findings with visualization.",
                           True, False, "none", "led", "team")])
    b.project("University Datathon", "hackathon", "Team of 4; A/B testing analysis for a retail sponsor", end=8,
              complexity="partly", quality=(1, 1, 1, 1, 1), bullets=[
                  b.bullet("Led the experiment design and SQL analysis; team placed 2nd of 30.",
                           True, False, "none", "led", "team")])
    b.skills_list("Python", "SQL", "pandas", "scikit-learn", "Git", proof="artifact")
    b.github_aligned()
    for name in DS_STRONG_SKILLS:
        b.skill("skill_evidence", name, "bullet")
        b.skill("applied_level", name, "skill_task")
    b.skill("skill_evidence", "Forecasting", "bullet")
    b.skill("applied_level", "Forecasting", "skill_task_result")
    for tool in ("Python", "SQL", "pandas", "scikit-learn", "Git"):
        b.skill("tool_evidence", tool, "used")
    return b.save()


def career_switcher(conn):
    b = Builder(conn, "Career switcher")
    b.role("Marketing Analyst", "Brandly", start=35, current=True, relevance="adjacent", scope="partly",
           complexity="partly", quality=(1, 1, 1, 1, 1), bullets=[
               b.bullet("Built campaign dashboards in Tableau with SQL; cut weekly reporting time 30%.",
                        True, True, "operational", "led", "cross_functional"),
               b.bullet("Ran A/B testing on email subject lines; open rate rose 8%.",
                        True, True, "business", "contributed", "team"),
           ])
    b.project("Customer segmentation", "project", "Clustering (machine learning) of customers with Python",
              end=2, complexity="partly", quality=(1, 0, 0, 1, 1), bullets=[
                  b.bullet("Segmented 50k customers with k-means clustering in scikit-learn.",
                           True, False, "none", "led", "own_work")])
    b.skills_list("Python", "SQL", "Tableau", "scikit-learn", proof="bullet")
    for name, ev, applied in [("Machine learning", "bullet", "skill_task"),
                              ("Experimentation / A/B testing", "bullet", "skill_task_result"),
                              ("Data visualization and communication", "bullet", "skill_task_result")]:
        b.skill("skill_evidence", name, ev)
        b.skill("applied_level", name, applied)
    for tool in ("Python", "SQL", "scikit-learn", "BI tool"):
        b.skill("tool_evidence", tool, "used")
    return b.save()


def stale_expert(conn):
    b = Builder(conn, "Stale expert")
    b.role("Data Scientist", "OldCo", start=96, end=60, scope="meets", complexity="meets", quality=(1, 1, 1, 1, 1),
           bullets=[
               b.bullet("Built a machine learning classification model in Python that cut fraud losses 20%.",
                        True, True, "business", "led", "organization"),
               b.bullet("Designed A/B testing with hypothesis testing for checkout; conversion rose 3%.",
                        True, True, "business", "led", "cross_functional"),
           ])
    b.role("Store Manager", "ShopRight", start=59, current=True, relevance="unrelated", scope="below",
           quality=(0, 1, 1, 1, 1), bullets=[
               b.bullet("Managed a team of 12 and grew store revenue 6%.", True, True, "business", "led", "team")])
    b.skills_list("Python", "SQL", "scikit-learn", proof="bullet")
    for name, ev in [("Machine learning", "bullet"), ("Statistical analysis", "bullet"),
                     ("Experimentation / A/B testing", "bullet")]:
        b.skill("skill_evidence", name, ev)
        b.skill("applied_level", name, "skill_task_result")
    for tool in ("Python", "SQL", "scikit-learn"):
        b.skill("tool_evidence", tool, "used")
    return b.save()


def resume_only(conn):
    b = Builder(conn, "Resume only")
    b.role("Junior Data Scientist", "Datawise", start=14, current=True, scope="partly", complexity="partly",
           quality=(1, 1, 1, 1, 1), bullets=[
               b.bullet("Built a machine learning model in Python that improved lead scoring accuracy 9%.",
                        True, True, "business", "led", "team")])
    b.skills_list("Python", "SQL", proof="bullet")
    b.skill("skill_evidence", "Machine learning", "bullet")
    b.skill("applied_level", "Machine learning", "skill_task_result")
    b.skill("tool_evidence", "Python", "used")
    return b.save()


def empty_profile(conn):
    """07 §2: one role, no bullets, no skills, no other sources."""
    b = Builder(conn, "Empty profile")
    b.role("Assistant", None, start=12, current=True, relevance="unrelated", scope="below", quality=(0, 0, 1, 0, 0))
    return b.save()


ARCHETYPES = {
    "strong_ds": strong_ds,
    "keyword_heavy": keyword_heavy,
    "project_strong_grad": project_strong_grad,
    "career_switcher": career_switcher,
    "stale_expert": stale_expert,
    "resume_only": resume_only,
}
