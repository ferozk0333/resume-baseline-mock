"""Trust: proof_density | validation_strength | consistency | evidence_quality (specs/04 §5).

Proof density, consistency and evidence quality use job-independent judgments only,
so a second baseline reuses them without model calls. Validation strength needs
"relevant" work and certifications, which depend on the baseline (reference_confirms
is job-dependent; certificate relevance is matched against baseline criteria).
"""
from __future__ import annotations

from core.text import contains_phrase
from graph.context import Ask, Ctx, Labels, evidence_json, project_text, role_text
from graph.nodes.base import Subcategory
from graph.nodes.expertise import repo_lines
from scoring import formulas as f
from scoring.matching import match_certification

# --- Proof density -----------------------------------------------------------------------

ARTIFACT_KINDS = {"case_study", "work_sample", "presentation", "publication"}


def _artifacts(ctx: Ctx) -> list[str]:
    out = repo_lines(ctx)
    for p in ctx.counted_projects():
        if not p.is_github and (p.kind in ARTIFACT_KINDS or ctx.evidence_type(p.evidence_id) == "portfolio"):
            out.append(project_text(p))
    out += [f"Certificate: {c.name}" + (f" ({c.issuer})" if c.issuer else "") for c in ctx.profile.certifications]
    return out


def _claims(ctx: Ctx):
    return [c for c in ctx.profile.claims if c.kind in ("skill", "tool")]


def proof_plan(ctx: Ctx) -> list[Ask]:
    artifacts = _artifacts(ctx)
    bullets = [b.text for item in [*ctx.profile.roles, *ctx.counted_projects()] for b in item.bullets]
    asks = []
    for c in _claims(ctx):
        names = {c.name_raw, c.name_norm}
        context = {"claim": c.name_raw,
                   "artifacts": [a for a in artifacts if any(contains_phrase(a, n) for n in names)],
                   "bullets": [b for b in bullets if any(contains_phrase(b, n) for n in names)]}
        if not context["artifacts"] and not context["bullets"]:
            asks.append(Ask("proof_support", "claim", c.id, context, rule_label="claimed_only",
                            rule_reason="Not mentioned in any bullet or artifact."))
        else:
            asks.append(Ask("proof_support", "claim", c.id, context))
    return asks


def proof_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.proof_density([(c.name_norm, labels.get("proof_support", c.id)) for c in _claims(ctx)])


# --- Validation strength ----------------------------------------------------------------

def validation_plan(ctx: Ctx) -> list[Ask]:
    return [Ask("reference_confirms", "reference", r.id,
                {"baseline": ctx.baseline_summary,
                 "reference": f"Relationship: {r.relationship or 'not stated'}\nStatement: {r.statement or ''}"})
            for r in ctx.profile.references]


def validation_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    refs = []
    for r in ctx.profile.references:
        label = labels.get("reference_confirms", r.id)
        refs.append(f.ReferenceIn(r.id, r.relationship, None if label is None else label == "confirms_relevant_work"))
    certs = [f.CertificationIn(c.id, c.verified, match_certification(c.name, ctx.baseline.criteria))
             for c in ctx.profile.certifications]
    return f.validation_strength(refs, certs)


# --- Consistency ------------------------------------------------------------------------

def _summary(extracted: dict) -> list[str]:
    lines = []
    for r in extracted.get("roles", []):
        dates = f"{r.get('start_date') or '?'} to {'present' if r.get('is_current') else r.get('end_date') or '?'}"
        lines.append(f"Role: {r['title']}" + (f" at {r['employer']}" if r.get("employer") else "") + f" ({dates})")
    lines += [f"Project: {p['name']}" for p in extracted.get("projects", [])]
    claims = extracted.get("claims", [])
    skills = [c["name_raw"] for c in claims if c.get("kind") in ("skill", "tool")]
    awards = [c["name_raw"] for c in claims if c.get("kind") == "award"]
    if skills:
        lines.append("Skills and tools: " + ", ".join(skills))
    if awards:                                       # an award is never a skill (cleanup round, open question 45)
        lines.append("Awards: " + ", ".join(awards))
    return lines


def _sources(ctx: Ctx) -> tuple[list[str], list[tuple[str, str, list[str]]]]:
    """(resume summary, [(subject_id, source type, source summary)]) for non-resume sources present."""
    validated = [e for e in ctx.evidence.values() if e["status"] == "validated"]
    resume = next((e for e in validated if e["type"] == "resume"), None)
    if resume is None:
        return [], []
    sources = [(e["id"], e["type"], _summary(evidence_json(ctx, e["id"])))
               for e in validated if e["type"] in ("linkedin_pdf", "portfolio")]
    repos = repo_lines(ctx)
    if repos:
        sources.append((f"github:{ctx.candidate_id}", "github", repos))
    return _summary(evidence_json(ctx, resume["id"])), [s for s in sources if s[2]]


def consistency_plan(ctx: Ctx) -> list[Ask]:
    resume, sources = _sources(ctx)
    return [Ask("source_alignment", "evidence_item", sid, {"resume": resume, "source": summary})
            for sid, _, summary in sources]


def consistency_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    _, sources = _sources(ctx)
    return f.consistency([f.SourceIn(sid, kind, None if labels.get("source_alignment", sid) is None
                                     else labels.get("source_alignment", sid) == "aligned")
                          for sid, kind, _ in sources])


# --- Evidence quality ------------------------------------------------------------------

def _quality_items(ctx: Ctx):
    for r in ctx.profile.roles:
        yield "role", r.id, role_text(r)
    for p in ctx.counted_projects():
        yield "project", p.id, project_text(p)


def quality_plan(ctx: Ctx) -> list[Ask]:
    return [Ask(f"quality_check_{i}", kind, item_id, {"item": text})
            for kind, item_id, text in _quality_items(ctx) for i in range(1, 6)]


def quality_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.evidence_quality([
        f.QualityIn(item_id, tuple(labels.yes(f"quality_check_{i}", item_id) for i in range(1, 6)))
        for _, item_id, _ in _quality_items(ctx)
    ])


SUBCATEGORIES = [
    Subcategory("proof_density", proof_plan, proof_compute),
    Subcategory("validation_strength", validation_plan, validation_compute),
    Subcategory("consistency", consistency_plan, consistency_compute),
    Subcategory("evidence_quality", quality_plan, quality_compute),
]
