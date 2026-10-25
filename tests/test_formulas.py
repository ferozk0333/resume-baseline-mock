"""Phase 2: scoring formulas and aggregation with hand-made labels (specs/07 §1)."""
from datetime import date

import pytest

from core import config
from scoring import formulas as f
from scoring.aggregate import aggregate, normalize_weights

TODAY = date(2026, 6, 15)


def ago(months: int) -> date:
    """First day of the month `months` before TODAY's month."""
    idx = TODAY.year * 12 + TODAY.month - 1 - months
    return date(idx // 12, idx % 12 + 1, 1)


def role(id="r", start=0, end=None, current=False, rel="relevant"):
    """start/end are 'months ago'; end=None with current=False means end = start."""
    return f.RoleIn(id, ago(start), None if end is None else ago(end), current, rel)


def proj(id="p", kind="project", rel="relevant", end=None, **kw):
    return f.ProjectIn(id, kind, rel, end=None if end is None else ago(end), **kw)


approx = pytest.approx


# --- Dates ---------------------------------------------------------------------

def test_role_months_inclusive_and_current():
    assert f.role_months(role(start=23, end=0), TODAY) == 24
    assert f.role_months(role(start=11, current=True), TODAY) == 12
    assert f.role_months(role(start=5), TODAY) == 1  # no end, not current → end = start


def test_future_or_reversed_dates_excluded():
    future = f.RoleIn("x", date(2027, 1, 1), None, True, "relevant")
    reversed_ = f.RoleIn("y", ago(1), ago(5), False, "relevant")
    assert f.role_span(future, TODAY) is None
    assert f.role_span(reversed_, TODAY) is None
    assert f.role_span(f.RoleIn("z", None, ago(1)), TODAY) is None


# --- Relevance -------------------------------------------------------------------

def test_relevance_weighted_by_months():
    roles = [role("a", 35, 12, rel="relevant"), role("b", 11, current=True, rel="adjacent")]
    s = f.relevance(roles, TODAY)
    assert s.status == "scored" and s.score == approx(30 / 36)
    assert round(s.score, 2) == 0.83


def test_relevance_no_roles_missing_with_guidance():
    s = f.relevance([], TODAY)
    assert s.status == "missing" and s.score == 0 and s.guidance


def test_relevance_all_judgments_failed_is_error():
    s = f.relevance([role(rel=None, current=True)], TODAY)
    assert s.status == "error" and s.score is None


def test_relevance_failed_judgment_skipped_when_others_usable():
    s = f.relevance([role("a", 11, current=True), role("b", 30, 20, rel=None)], TODAY)
    assert s.status == "scored" and s.score == 1.0


def test_relevance_future_role_excluded_from_math():
    future = f.RoleIn("x", date(2027, 1, 1), date(2027, 6, 1), False, "unrelated")
    s = f.relevance([role("a", 11, current=True), future], TODAY)
    assert s.score == 1.0


# --- Depth -----------------------------------------------------------------------

def test_depth_overlapping_months_counted_once():
    roles = [role("a", 23, current=True), role("b", 11, current=True)]
    s = f.depth(roles, [], required_years=4, today=TODAY)
    assert s.details["relevant_years"] == approx(2.0)
    assert s.score == approx(0.5)


def test_depth_overlap_takes_highest_relevance_per_month():
    roles = [role("a", 11, current=True, rel="adjacent"), role("b", 11, current=True, rel="relevant")]
    s = f.depth(roles, [], required_years=4, today=TODAY)
    assert s.details["relevant_years"] == approx(1.0)


def test_depth_required_years_zero_treated_as_one():
    s = f.depth([role(start=5, current=True)], [], required_years=0, today=TODAY)
    assert s.score == approx(0.5)


def test_depth_entry_level_takes_max_of_work_and_projects():
    projects = [proj("p1"), proj("p2", "hackathon"), proj("p3", "publication")]
    s = f.depth([role(start=5, current=True)], projects, required_years=1, today=TODAY)
    assert s.details["work_score"] == approx(0.5) and s.details["project_score"] == 1.0
    assert s.score == 1.0


def test_depth_non_entry_ignores_projects():
    projects = [proj("p1"), proj("p2"), proj("p3")]
    s = f.depth([role(start=17, current=True)], projects, required_years=3, today=TODAY)
    assert s.score == approx(0.5)
    assert s.details["project_score"] is None


def test_depth_forked_repo_without_own_commits_not_counted():
    projects = [
        proj("fork0", "repo", is_fork=True, own_commits=0),
        proj("fork3", "repo", is_fork=True, own_commits=3),
        proj("own", "repo", is_fork=False, own_commits=0),
    ]
    s = f.depth([], projects, required_years=1, today=TODAY)
    assert set(s.details["counted_project_ids"]) == {"fork3", "own"}
    assert s.score == approx(2 / 3)


def test_depth_projects_use_relevance_values():
    projects = [proj("a", rel="adjacent"), proj("b", rel="unrelated"), proj("c")]
    s = f.depth([], projects, required_years=1, today=TODAY)
    assert s.score == approx(1.5 / 3)


def test_depth_nothing_to_count_is_missing():
    s = f.depth([], [proj("w", "work_sample")], required_years=1, today=TODAY)
    assert s.status == "missing" and s.guidance
    assert f.depth([], [proj()], required_years=3, today=TODAY).status == "missing"


# --- Scope / Progression ------------------------------------------------------

def test_scope_role_selection_prefers_relevant_then_recent():
    roles = [
        role("adj_now", 5, current=True, rel="adjacent"),
        role("rel_old", 40, 30),
        role("rel_newer", 29, 20),
        role("unrel", 3, current=True, rel="unrelated"),
    ]
    assert f.select_scope_role(roles, TODAY).id == "rel_newer"
    assert f.select_scope_role([roles[0], roles[3]], TODAY).id == "adj_now"
    assert f.select_scope_role([roles[3]], TODAY) is None


def test_scope_values_and_missing():
    r = role()
    assert [f.scope(r, label).score for label in ("meets", "partly", "below")] == [1.0, 0.5, 0.0]
    assert f.scope(None, None).status == "missing"
    assert f.scope(r, None).status == "error"


def test_progression_one_role_is_na():
    s = f.progression(1, None)
    assert s.status == "na" and s.score is None
    assert f.progression(2, "more_impactful").score == 1.0
    assert f.progression(3, "same").score == 0.5
    assert f.progression(2, "less").score == 0.0


# --- Recency ---------------------------------------------------------------------

@pytest.mark.parametrize("ended,expected", [(12, 0.90), (18, 0.854), (36, 0.729), (60, 0.590)])
def test_recency_decay(ended, expected):
    s = f.recency([role(start=ended + 12, end=ended)], [], required_years=1, today=TODAY)
    assert s.score == approx(expected, abs=0.0005)


def test_recency_ongoing_and_adjacent():
    assert f.recency([role(start=3, current=True)], [], 1, TODAY).score == 1.0
    assert f.recency([role(start=3, current=True, rel="adjacent")], [], 1, TODAY).score == 0.5


def test_recency_entry_counts_projects_non_entry_ignores_them():
    roles = [role(start=72, end=60)]
    projects = [proj("p", end=0)]
    assert f.recency(roles, projects, required_years=1, today=TODAY).score == 1.0
    assert f.recency(roles, projects, required_years=3, today=TODAY).score == approx(0.9 ** 5)


def test_recency_project_kinds():
    # case studies / work samples are not in the entry-level list in 04
    s = f.recency([], [proj("c", "case_study", end=0)], 1, TODAY)
    assert s.status == "missing"
    assert f.recency([], [proj("h", "hackathon", end=0)], 1, TODAY).score == 1.0


def test_recency_missing_and_unrelated():
    assert f.recency([], [], 1, TODAY).status == "missing"
    s = f.recency([role(start=3, current=True, rel="unrelated")], [], 1, TODAY)
    assert s.status == "scored" and s.score == 0.0


def test_recency_stale_expert():
    roles = [role("old", 84, 60, rel="relevant"), role("now", 59, current=True, rel="unrelated")]
    assert f.recency(roles, [], 1, TODAY).score == approx(0.59, abs=0.001)


# --- Expertise -------------------------------------------------------------------

def test_role_skills_required_and_preferred():
    req = {"a": "bullet", "b": "bullet", "c": "keyword"}
    assert f.role_skills(req, {}).score == approx(0.8333, abs=1e-4)
    assert f.role_skills(req, {"d": "missing"}).score == approx(0.5556, abs=1e-4)


def test_combine_required_preferred_na_sides():
    assert f.combine_required_preferred(0.6, None) == 0.6
    assert f.combine_required_preferred(None, 0.3) == 0.3
    assert f.combine_required_preferred(None, None) is None
    assert f.role_skills({}, {}).status == "na"
    assert f.role_skills({}, {"x": "bullet"}).score == 1.0


def test_applied_knowledge_team_doc_example():
    s = f.applied_knowledge({"a": "skill_task_result", "b": "skill_task", "c": "mention_or_missing"}, {})
    assert s.score == 0.5


def test_tools_levels():
    assert f.tools({"Python": "listed"}, {}).score == 0.5
    assert f.tools({"Python": "used"}, {}).score == 1.0
    assert f.tools({"Python": "certified"}, {}).score == config.UNVERIFIED_CERT_TOOL_SCORE
    assert f.tools({"Python": "missing"}, {}).score == 0.0


def test_unverified_cert_tool_score_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "UNVERIFIED_CERT_TOOL_SCORE", 0.5)
    assert f.tools({"Python": "certified"}, {}).score == 0.5


def test_criteria_failed_judgments():
    s = f.role_skills({"a": "bullet", "b": None}, {})
    assert s.score == 1.0 and s.details["required"]["failed"] == ["b"]
    assert f.role_skills({"a": None}, {"b": None}).status == "error"


def test_unknown_label_raises():
    with pytest.raises(ValueError, match="unknown skill_evidence label"):
        f.role_skills({"a": "strong"}, {})


def test_complexity_max_over_relevant_items():
    items = [
        f.GradedItem("a", "relevant", "partly"),
        f.GradedItem("b", "unrelated", "meets"),  # ignored: relevance 0
        f.GradedItem("c", "adjacent", "no"),
    ]
    s = f.complexity(items)
    assert s.score == 0.5 and s.details["best"] == "a"
    assert f.complexity([f.GradedItem("b", "unrelated", None)]).status == "missing"
    assert f.complexity([f.GradedItem("a", "relevant", None)]).status == "error"


def test_work_samples():
    assert f.work_samples([]).status == "missing"
    assert f.work_samples([f.WorkSampleIn("r", "repo", True, counted=False)]).status == "missing"
    assert f.work_samples([f.WorkSampleIn("r", "repo", False)]).score == 0.0
    assert f.work_samples([f.WorkSampleIn("r", "repo", False), f.WorkSampleIn("p", "portfolio", True)]).score == 1.0


# --- Authority -------------------------------------------------------------------

def test_outcomes_multiplied_by_relevance():
    b = dict(has_metric=True, has_direction=True)
    assert f.outcomes([f.BulletIn("x", "unrelated", **b)]).score == 0.0
    assert f.outcomes([f.BulletIn("x", "relevant", **b)]).score == 1.0
    assert f.outcomes([f.BulletIn("x", "relevant", has_metric=True, has_direction=False)]).score == 0.5
    assert f.outcomes([f.BulletIn("x", "adjacent", **b)]).score == 0.5


def test_impact_and_ownership_max_level_times_relevance():
    bullets = [
        f.BulletIn("a", "adjacent", impact="business", ownership="led"),
        f.BulletIn("b", "relevant", impact="operational", ownership="contributed"),
        f.BulletIn("c", "unrelated", impact="business", ownership="led"),
    ]
    assert f.impact(bullets).score == 0.5
    assert f.ownership(bullets).score == 0.5
    bullets.append(f.BulletIn("d", "relevant", impact="business", ownership="led"))
    assert f.impact(bullets).score == 1.0 and f.ownership(bullets).score == 1.0


def test_authority_missing_and_error():
    for fn in (f.outcomes, f.impact, f.ownership):
        s = fn([])
        assert s.status == "missing" and s.guidance
    assert f.impact([f.BulletIn("a", "relevant")]).status == "error"


@pytest.mark.parametrize("reach,expected", [("cross_functional", 1.0), ("team", 0.5), ("own_work", 0.0), ("external", 1.0)])
def test_scope_of_impact(reach, expected):
    assert f.scope_of_impact([f.BulletIn("a", "relevant", reach=reach)], expected_reach=3).score == expected


def test_scope_of_impact_missing_cases():
    assert f.scope_of_impact([], 3).status == "missing"
    assert f.scope_of_impact([f.BulletIn("a", "relevant", reach="not_stated")], 3).status == "missing"
    # reach stated only in an unrelated bullet does not count [OPEN relevance filter]
    assert f.scope_of_impact([f.BulletIn("a", "unrelated", reach="external")], 3).status == "missing"


def test_recognition():
    assert f.recognition([True], []).score == 0.5
    assert f.recognition([True], [True]).score == 1.0
    s = f.recognition([], [])
    assert s.status == "missing" and s.score == 0 and s.guidance
    assert f.recognition([False], [False]).status == "missing"


# --- Trust -------------------------------------------------------------------------

def test_proof_density_example():
    claims = [(f"a{i}", "artifact") for i in range(4)] + [(f"b{i}", "bullet") for i in range(3)] \
        + [(f"c{i}", "claimed_only") for i in range(3)]
    assert f.proof_density(claims).score == approx(0.55)


def test_proof_density_dedupes_by_name_keeping_best():
    s = f.proof_density([("python", "claimed_only"), ("python", "artifact"), ("sql", "bullet")])
    assert s.score == approx(0.75)
    assert f.proof_density([]).status == "missing"


def test_validation_strength():
    mgr = f.ReferenceIn("r1", "manager", True)
    peer = f.ReferenceIn("r2", "peer", True)
    cert = f.CertificationIn("c1", verified=True, matched_criterion="Cloud ML certification")
    unverified = f.CertificationIn("c2", verified=False, matched_criterion="Cloud ML certification")
    assert f.validation_strength([mgr], [cert]).score == 1.0
    assert f.validation_strength([peer], [cert]).score == 0.5
    assert f.validation_strength([], []).status == "missing"
    s = f.validation_strength([peer], [unverified])
    assert s.status == "missing" and s.guidance
    assert f.validation_strength([f.ReferenceIn("r", "Supervisor", True)], []).score == 1.0


def test_consistency():
    assert f.consistency([]).status == "missing"
    assert f.consistency([f.SourceIn("g", "github", False), f.SourceIn("l", "linkedin_pdf", True)]).score == 1.0
    assert f.consistency([f.SourceIn("g", "github", False)]).score == 0.0


def test_evidence_quality_average():
    items = [
        f.QualityIn("a", (True, True, True, True, False)),
        f.QualityIn("b", (True, True, True, False, False)),
        f.QualityIn("c", (True,) * 5),
    ]
    assert f.evidence_quality(items).score == approx(0.8)
    assert f.evidence_quality([]).status == "missing"


# --- Aggregation ---------------------------------------------------------------

def _scored(x):
    return f.SubScore("scored", x)


def test_worked_check_section_8():
    """04 §8: one current relevant role of 24 months, no projects, entry baseline (2 years)."""
    roles = [role("r", 23, current=True)]
    subs = {
        "relevance": f.relevance(roles, TODAY),
        "depth": f.depth(roles, [], 2, TODAY),
        "scope": f.scope(roles[0], "partly"),
        "progression": f.progression(len(roles), None),
        "recency": f.recency(roles, [], 2, TODAY),
    }
    assert subs["relevance"].score == 1.0
    assert subs["depth"].score == 1.0 and subs["depth"].details["project_score"] == 0
    assert subs["recency"].score == 1.0
    assert subs["progression"].status == "na"
    agg = aggregate(subs)
    assert agg.dimensions["experience"] == approx(80 / 90)
    assert round(agg.dimensions["experience"], 3) == 0.889
    assert "progression" in agg.excluded["experience"]


def test_na_does_not_change_other_scores_and_drops_its_weight():
    base = {s: _scored(0.5) for s in config.SUBCATEGORY_DIMENSION}
    base["relevance"] = _scored(1.0)
    with_na = dict(base, progression=f.SubScore("na", None))
    a, b = aggregate(base), aggregate(with_na)
    # experience: (35×1 + 25×.5 + 20×.5 + 10×.5) / 90 without progression
    assert b.dimensions["experience"] == approx((35 + 12.5 + 10 + 5) / 90)
    assert a.dimensions["experience"] == approx((35 + 12.5 + 10 + 5 + 5) / 100)
    for d in ("expertise", "authority", "trust"):
        assert a.dimensions[d] == b.dimensions[d]
    assert "progression" not in b.effective_weights


def test_missing_counts_as_zero_error_excluded():
    subs = {s: _scored(1.0) for s in config.SUBCATEGORY_DIMENSION}
    subs["recognition"] = f.SubScore("missing", 0.0, guidance="x")
    assert aggregate(subs).dimensions["authority"] == approx(0.9)
    subs["recognition"] = f.SubScore("error", None)
    assert aggregate(subs).dimensions["authority"] == 1.0


def test_dimension_with_no_included_subcategories_is_dropped():
    subs = {s: _scored(1.0) for s in config.SUBCATEGORY_DIMENSION}
    for s in config.SUBCATEGORIES["trust"]:
        subs[s] = f.SubScore("na", None)
    for s in config.SUBCATEGORIES["experience"]:
        subs[s] = _scored(0.0)
    agg = aggregate(subs)
    assert agg.dimensions["trust"] is None
    assert agg.final == approx(100 * (30 * 0 + 30 * 1 + 20 * 1) / 80)


def test_weight_groups_normalize_to_one():
    w = normalize_weights(config.default_weights())
    assert sum(w["dimensions"].values()) == approx(1)
    for d in config.DIMENSIONS:
        assert sum(w["subcategories"][d].values()) == approx(1)
    assert w["subcategories"]["trust"]["proof_density"] == approx(30 / 95)


def test_effective_weights_sum_to_one():
    subs = {s: _scored(0.5) for s in config.SUBCATEGORY_DIMENSION}
    subs["progression"] = f.SubScore("na", None)
    agg = aggregate(subs)
    assert sum(agg.effective_weights.values()) == approx(1)
    assert agg.effective_weights["relevance"] == approx(0.3 * 35 / 90)


def test_slider_change_matches_hand_calculation():
    subs = {}
    for d, value in {"experience": 1.0, "expertise": 0.0, "authority": 0.5, "trust": 1.0}.items():
        subs.update({s: _scored(value) for s in config.SUBCATEGORIES[d]})
    assert aggregate(subs).final == approx(100 * (0.3 * 1 + 0.3 * 0 + 0.2 * 0.5 + 0.2 * 1))  # 60

    w = config.default_weights()
    w["dimensions"]["experience"] = 60
    assert aggregate(subs, w).final == approx(100 * (60 + 0 + 10 + 20) / 130)

    w = config.default_weights()
    w["subcategories"]["experience"]["relevance"] = 0
    subs["relevance"] = _scored(0.0)
    assert aggregate(subs, w).dimensions["experience"] == 1.0  # zero-weight sub has no effect


def test_aggregate_accepts_db_style_dicts_and_rejects_bad_scores():
    subs = {s: {"status": "scored", "score": 1.0} for s in config.SUBCATEGORY_DIMENSION}
    assert aggregate(subs).final == approx(100)
    subs["depth"] = {"status": "scored", "score": 1.5}
    with pytest.raises(ValueError):
        aggregate(subs)
    w = config.default_weights()
    w["dimensions"]["trust"] = -1
    with pytest.raises(ValueError):
        normalize_weights(w)


def test_all_zero_weights_give_no_final():
    w = config.default_weights()
    w["dimensions"] = {d: 0 for d in config.DIMENSIONS}
    agg = aggregate({s: _scored(1.0) for s in config.SUBCATEGORY_DIMENSION}, w)
    assert agg.final is None and agg.effective_weights == {}


# --- Phase 2 follow-ups -----------------------------------------------------------

def test_month_rule_role_length_counts_start_and_end_month():
    jan_dec = f.RoleIn("y", date(2024, 1, 1), date(2024, 12, 1))
    assert f.role_months(jan_dec, TODAY) == 12
    same_month = f.RoleIn("m", date(2025, 3, 1), date(2025, 3, 1))
    assert f.role_months(same_month, TODAY) == 1


def test_month_rule_recency_gap_is_end_month_to_current_month():
    # TODAY is 2026-06-15; a role ending 2025-06 is 12 months back, regardless of day of month.
    ended = f.RoleIn("e", date(2024, 1, 1), date(2025, 6, 30), False, "relevant")
    s = f.recency([ended], [], 1, TODAY)
    assert s.details["activities"][0]["gap_years"] == 1.0 and s.score == approx(0.9)
    this_month = f.RoleIn("t", date(2026, 1, 1), date(2026, 6, 1), False, "relevant")
    assert f.recency([this_month], [], 1, TODAY).score == 1.0


def test_recency_repos_do_not_count_even_for_entry_level():
    s = f.recency([], [proj("r", "repo", end=0, own_commits=5)], 1, TODAY)
    assert s.status == "missing"


def test_scope_of_impact_relevance_filter_can_be_turned_off(monkeypatch):
    bullets = [f.BulletIn("a", "unrelated", reach="cross_functional")]
    assert f.scope_of_impact(bullets, 3).status == "missing"
    monkeypatch.setattr(config, "SCOPE_OF_IMPACT_RELEVANCE_FILTER", False)
    s = f.scope_of_impact(bullets, 3)
    assert s.status == "scored" and s.score == 1.0


def test_unmatched_certification_recorded_as_not_matched():
    cert = f.CertificationIn("c", verified=True)
    s = f.validation_strength([], [cert])
    assert s.status == "missing"
    assert s.details["certifications"] == [
        {"id": "c", "verified": True, "match": "not_matched", "criterion": None}
    ]
    judged = f.CertificationIn("c", verified=True, judged_relevant=True)
    assert f.validation_strength([], [judged]).score == 0.5


def test_match_certification_uses_name_and_aliases_case_insensitively():
    from core.models import BaselineCriterion
    from scoring.matching import match_certification
    crit = [
        BaselineCriterion(kind="certification", name="Cloud ML certification",
                          aliases=["AWS ML", "Google Professional ML Engineer"], priority="preferred"),
        BaselineCriterion(kind="tool", name="Python", priority="required"),
    ]
    assert match_certification("aws ml specialty", crit) == "Cloud ML certification"
    assert match_certification("Google Professional ML Engineer (2025)", crit) == "Cloud ML certification"
    assert match_certification("CLOUD ML CERTIFICATION", crit) == "Cloud ML certification"
    # generic real title with no alias overlap → not matched (judge fallback is a proposal)
    assert match_certification("AWS Certified Machine Learning - Specialty", crit) is None
    assert match_certification("Python Institute PCAP", crit) is None  # tool criteria ignored
    assert match_certification("AWS MLOps", crit) is None  # whole-phrase only


def test_dimension_with_all_zero_subcategory_weights_is_excluded():
    subs = {s: _scored(1.0) for s in config.SUBCATEGORY_DIMENSION}
    for s in config.SUBCATEGORIES["authority"]:
        subs[s] = _scored(0.0)
    w = config.default_weights()
    w["subcategories"]["authority"] = {s: 0 for s in config.SUBCATEGORIES["authority"]}
    agg = aggregate(subs, w)
    assert agg.dimensions["authority"] is None
    assert agg.final == approx(100.0)  # other three dimensions (all 1.0) renormalize
    assert not any(s in agg.effective_weights for s in config.SUBCATEGORIES["authority"])
    assert sum(agg.effective_weights.values()) == approx(1)


def test_no_roles_flag_makes_relevance_and_scope_na(monkeypatch):
    assert f.relevance([], TODAY).status == "missing"
    assert f.scope(None, None, has_roles=False).status == "missing"
    monkeypatch.setattr(config, "NO_ROLES_RELEVANCE_SCOPE_NA", True)
    assert f.relevance([], TODAY).status == "na"
    assert f.scope(None, None, has_roles=False).status == "na"
    assert f.scope(None, None, has_roles=True).status == "missing"   # roles exist, none relevant
