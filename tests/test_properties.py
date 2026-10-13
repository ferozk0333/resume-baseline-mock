"""Phase 2 property tests on formulas and aggregation (specs/07 §2).

Pipeline-level properties (empty profile vs archetypes, no model calls on
re-aggregation) need the graph and are added in phase 5.
"""
from datetime import date

from hypothesis import given, settings, strategies as st

from core import config
from scoring import formulas as f
from scoring.aggregate import aggregate

TODAY = date(2026, 6, 15)
REL_ORDER = ["unrelated", "adjacent", "relevant"]

settings.register_profile("default", max_examples=150, deadline=None)
settings.load_profile("default")


def ago(months: int) -> date:
    idx = TODAY.year * 12 + TODAY.month - 1 - months
    return date(idx // 12, idx % 12 + 1, 1)


rel_labels = st.sampled_from(REL_ORDER)


@st.composite
def roles_st(draw, min_size=0):
    n = draw(st.integers(min_size, 5))
    roles = []
    for i in range(n):
        start = draw(st.integers(0, 120))
        current = draw(st.booleans())
        end = None if current else draw(st.integers(0, start))
        roles.append(f.RoleIn(f"r{i}", ago(start), None if end is None else ago(end), current, draw(rel_labels)))
    return roles


@st.composite
def projects_st(draw):
    n = draw(st.integers(0, 5))
    return [
        f.ProjectIn(
            f"p{i}",
            draw(st.sampled_from(["project", "hackathon", "publication", "case_study", "repo", "work_sample"])),
            draw(rel_labels),
            end=ago(draw(st.integers(0, 120))),
            is_fork=draw(st.booleans()),
            own_commits=draw(st.integers(0, 3)),
        )
        for i in range(n)
    ]


@st.composite
def bullets_st(draw, min_size=1):
    n = draw(st.integers(min_size, 6))
    return [
        f.BulletIn(
            f"b{i}", draw(rel_labels),
            has_metric=draw(st.booleans()), has_direction=draw(st.booleans()),
            impact=draw(st.sampled_from(list(f.IMPACT))),
            ownership=draw(st.sampled_from(list(f.OWNERSHIP))),
            reach=draw(st.sampled_from(list(f.REACH))),
        )
        for i in range(n)
    ]


def upgrade(label, order):
    i = order.index(label)
    return order[min(i + 1, len(order) - 1)]


def in_bounds(s: f.SubScore) -> bool:
    if s.status in ("na", "error"):
        return s.score is None
    return 0.0 <= s.score <= 1.0


def experience_scores(roles, projects, required_years):
    return [
        f.relevance(roles, TODAY),
        f.depth(roles, projects, required_years, TODAY),
        f.recency(roles, projects, required_years, TODAY),
    ]


# --- Bounds -------------------------------------------------------------------------

@given(roles_st(), projects_st(), st.sampled_from([0, 1, 2, 3, 5]))
def test_experience_bounds(roles, projects, years):
    assert all(in_bounds(s) for s in experience_scores(roles, projects, years))


@given(bullets_st(min_size=0), st.integers(1, 5))
def test_authority_bounds(bullets, expected_reach):
    for s in (f.outcomes(bullets), f.impact(bullets), f.ownership(bullets),
              f.scope_of_impact(bullets, expected_reach)):
        assert in_bounds(s)


@given(st.dictionaries(st.text(min_size=1, max_size=3), st.sampled_from(list(f.SKILL_EVIDENCE)), max_size=5),
       st.dictionaries(st.text(min_size=1, max_size=3), st.sampled_from(list(f.SKILL_EVIDENCE)), max_size=5))
def test_criteria_bounds(req, pref):
    assert in_bounds(f.role_skills(req, pref))


subscore_st = st.one_of(
    st.builds(f.SubScore, st.just("scored"), st.floats(0, 1)),
    st.just(f.SubScore("missing", 0.0)),
    st.just(f.SubScore("na", None)),
    st.just(f.SubScore("error", None)),
)
weight_st = st.floats(0, 100)


@st.composite
def weights_st(draw):
    w = config.default_weights()
    for d in config.DIMENSIONS:
        w["dimensions"][d] = draw(weight_st)
        for s in config.SUBCATEGORIES[d]:
            w["subcategories"][d][s] = draw(weight_st)
    return w


@given(st.fixed_dictionaries({s: subscore_st for s in config.SUBCATEGORY_DIMENSION}), weights_st())
def test_aggregate_bounds(subs, weights):
    agg = aggregate(subs, weights)
    for v in agg.dimensions.values():
        assert v is None or -1e-9 <= v <= 1 + 1e-9
    assert agg.final is None or -1e-9 <= agg.final <= 100 + 1e-9
    if agg.final is not None and sum(agg.effective_weights.values()) > 0:
        assert abs(sum(agg.effective_weights.values()) - 1) < 1e-9


# --- Monotonicity ----------------------------------------------------------------

@given(roles_st(min_size=1), projects_st(), st.sampled_from([1, 3]), st.data())
def test_upgrading_relevance_never_lowers_experience(roles, projects, years, data):
    i = data.draw(st.integers(0, len(roles) - 1))
    better = list(roles)
    better[i] = f.RoleIn(roles[i].id, roles[i].start, roles[i].end, roles[i].is_current,
                         upgrade(roles[i].relevance, REL_ORDER))
    for before, after in zip(experience_scores(roles, projects, years), experience_scores(better, projects, years)):
        assert after.score >= before.score - 1e-12


@given(roles_st(), projects_st(), st.data())
def test_upgrading_project_relevance_never_lowers_depth_or_recency(roles, projects, data):
    if not projects:
        return
    i = data.draw(st.integers(0, len(projects) - 1))
    p = projects[i]
    better = list(projects)
    better[i] = f.ProjectIn(p.id, p.kind, upgrade(p.relevance, REL_ORDER), p.start, p.end, p.is_fork, p.own_commits)
    for before, after in zip(experience_scores(roles, projects, 1), experience_scores(roles, better, 1)):
        assert after.score >= before.score - 1e-12


BULLET_ORDERS = {
    "relevance": REL_ORDER,
    "impact": ["none", "operational", "business"],
    "ownership": ["not_stated", "contributed", "led"],
    "reach": ["not_stated", "own_work", "team", "cross_functional", "organization", "external"],
}


@given(bullets_st(), st.integers(1, 5), st.sampled_from(sorted(BULLET_ORDERS)), st.data())
def test_upgrading_bullet_label_never_lowers_authority(bullets, expected_reach, field_name, data):
    i = data.draw(st.integers(0, len(bullets) - 1))
    b = bullets[i]
    fields = dict(vars(b))
    fields[field_name] = upgrade(fields[field_name], BULLET_ORDERS[field_name])
    better = list(bullets)
    better[i] = f.BulletIn(**fields)

    def scores(bs):
        return [f.outcomes(bs), f.impact(bs), f.ownership(bs), f.scope_of_impact(bs, expected_reach)]

    for before, after in zip(scores(bullets), scores(better)):
        assert after.score >= before.score


@given(st.lists(st.sampled_from(list(f.SKILL_EVIDENCE)), min_size=1, max_size=5),
       st.lists(st.sampled_from(list(f.SKILL_EVIDENCE)), max_size=5), st.data())
def test_upgrading_skill_evidence_never_lowers_score(req, pref, data):
    order = ["missing", "keyword", "bullet"]
    req_d = {f"r{i}": l for i, l in enumerate(req)}
    pref_d = {f"p{i}": l for i, l in enumerate(pref)}
    key = data.draw(st.sampled_from(sorted(req_d) + sorted(pref_d)))
    better_req, better_pref = dict(req_d), dict(pref_d)
    target = better_req if key in better_req else better_pref
    target[key] = upgrade(target[key], order)
    assert f.role_skills(better_req, better_pref).score >= f.role_skills(req_d, pref_d).score - 1e-12


@given(st.fixed_dictionaries({s: subscore_st for s in config.SUBCATEGORY_DIMENSION}),
       st.sampled_from(sorted(config.SUBCATEGORY_DIMENSION)), st.floats(0, 1))
def test_raising_a_subscore_never_lowers_final(subs, name, bump):
    if subs[name].status not in ("scored", "missing"):
        return
    before = aggregate(subs)
    raised = dict(subs)
    raised[name] = f.SubScore("scored", max(subs[name].score, bump))
    after = aggregate(raised)
    if before.final is not None:
        assert after.final >= before.final - 1e-9


# --- No volume gaming --------------------------------------------------------------

@given(bullets_st(), st.integers(1, 5))
def test_duplicating_bullets_changes_nothing(bullets, expected_reach):
    dup = bullets + [bullets[0]] * 10

    def scores(bs):
        return [s.score for s in (f.outcomes(bs), f.impact(bs), f.ownership(bs), f.scope_of_impact(bs, expected_reach))]

    assert scores(dup) == scores(bullets)


@given(roles_st(min_size=1), projects_st())
def test_duplicating_a_role_changes_nothing(roles, projects):
    dup = roles + [roles[0]] * 10
    for a, b in zip(experience_scores(roles, projects, 1), experience_scores(dup, projects, 1)):
        assert (a.score is None and b.score is None) or abs(a.score - b.score) < 1e-12


@given(roles_st(), projects_st())
def test_duplicating_a_project_changes_nothing(roles, projects):
    if not projects:
        return
    dup = projects + [projects[0]] * 10
    for a, b in zip(experience_scores(roles, projects, 1), experience_scores(roles, dup, 1)):
        assert (a.score is None and b.score is None) or abs(a.score - b.score) < 1e-12


@given(st.lists(st.tuples(st.sampled_from(["python", "sql", "git"]), st.sampled_from(list(f.PROOF_SUPPORT))), min_size=1))
def test_duplicating_claims_changes_nothing(claims):
    assert f.proof_density(claims * 10).score == f.proof_density(claims).score


# --- Determinism -------------------------------------------------------------------

@given(roles_st(), projects_st(), bullets_st(), weights_st())
def test_same_inputs_same_scores(roles, projects, bullets, weights):
    def run():
        subs = {
            "relevance": f.relevance(roles, TODAY),
            "depth": f.depth(roles, projects, 1, TODAY),
            "recency": f.recency(roles, projects, 1, TODAY),
            "outcomes": f.outcomes(bullets),
            "impact": f.impact(bullets),
        }
        agg = aggregate(subs, weights)
        return [(s.status, s.score) for s in subs.values()], agg.dimensions, agg.final

    assert run() == run()
