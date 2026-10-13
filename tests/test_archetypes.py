"""Phase 5: archetype ordering (specs/07 §3) and the empty-profile property (07 §2).

These check ordering, not exact numbers. If an assertion fails, the message lists
the subcategories that drive the gap; formulas are not tuned to make it pass.
"""
import pytest

from core import config, db
from graph.context import Deps
from graph.pipeline import Pipeline
from scoring.aggregate import aggregate
from tests.fixtures.archetypes import ARCHETYPES, TODAY, empty_profile

DS = "baseline-data-scientist-entry"


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("arch") / "a.db")
    conn = db.init_app_db(path)
    out = {}
    for name, build in {**ARCHETYPES, "empty": empty_profile}.items():
        cid, judge = build(conn)
        res = Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
        assert res["errors"] == [], (name, res["errors"])
        out[name] = res
    conn.close()
    return out


def final(results, name):
    return results[name]["final"]


def drivers(results, a, b) -> str:
    """Subcategories ranked by weighted contribution to final(a) - final(b)."""
    ea = aggregate(results[a]["subcategory_scores"]).effective_weights
    eb = aggregate(results[b]["subcategory_scores"]).effective_weights
    rows = []
    for s in config.SUBCATEGORY_DIMENSION:
        sa, sb = results[a]["subcategory_scores"][s], results[b]["subcategory_scores"][s]
        ca = 100 * ea.get(s, 0) * (sa["score"] or 0)
        cb = 100 * eb.get(s, 0) * (sb["score"] or 0)
        rows.append((ca - cb, s, sa["status"], sa["score"], sb["status"], sb["score"]))
    rows.sort()
    return "\n".join(f"  {d:+6.2f}  {s:20s} {a}: {st_a} {sc_a}   {b}: {st_b} {sc_b}"
                     for d, s, st_a, sc_a, st_b, sc_b in rows)


def assert_above(results, a, b):
    assert final(results, a) > final(results, b), (
        f"expected {a} ({final(results, a):.1f}) > {b} ({final(results, b):.1f}); drivers:\n"
        + drivers(results, a, b))


def test_print_scores(results, capsys):
    with capsys.disabled():
        print("\nArchetype scores vs Data Scientist (entry):")
        for name, r in sorted(results.items(), key=lambda kv: -kv[1]["final"]):
            dims = "  ".join(f"{d[:4]} {v:.2f}" if v is not None else f"{d[:4]}  n/a"
                             for d, v in r["dimension_scores"].items())
            print(f"  {name:20s} {r['final']:6.1f}   {dims}")


@pytest.mark.parametrize("other", ["keyword_heavy", "project_strong_grad", "career_switcher", "stale_expert",
                                   "resume_only"])
def test_strong_ds_is_highest(results, other):
    assert_above(results, "strong_ds", other)


def test_keyword_heavy_below_project_grad(results):
    assert_above(results, "project_strong_grad", "keyword_heavy")


def test_career_switcher_between_keyword_heavy_and_strong(results):
    assert_above(results, "career_switcher", "keyword_heavy")
    assert_above(results, "strong_ds", "career_switcher")


def test_stale_expert_recency_and_rank(results):
    rec = results["stale_expert"]["subcategory_scores"]["recency"]
    assert rec["status"] == "scored" and rec["score"] == pytest.approx(0.9 ** 5, abs=0.01)  # ≈ 0.59 × relevance 1
    assert_above(results, "strong_ds", "stale_expert")


def test_resume_only_missing_sources_have_guidance(results):
    subs = results["resume_only"]["subcategory_scores"]
    for name in ("consistency", "validation_strength", "work_samples"):
        assert subs[name]["status"] == "missing" and subs[name]["guidance"], name


def test_every_missing_subcategory_has_guidance(results):
    for name, r in results.items():
        for sub, s in r["subcategory_scores"].items():
            if s["status"] == "missing":
                assert s["guidance"], (name, sub)


@pytest.mark.parametrize("other", list(ARCHETYPES))
def test_empty_profile_below_every_archetype(results, other):
    assert_above(results, other, "empty")


def test_no_roles_flag_effect_on_project_grad(tmp_path, monkeypatch, capsys):
    """04 [OPEN] NO_ROLES_RELEVANCE_SCOPE_NA: report both numbers for the product decision."""
    from tests.fixtures.archetypes import project_strong_grad
    out = {}
    for flag in (False, True):
        monkeypatch.setattr(config, "NO_ROLES_RELEVANCE_SCOPE_NA", flag)
        path = str(tmp_path / f"grad_{flag}.db")
        conn = db.init_app_db(path)
        cid, judge = project_strong_grad(conn)
        conn.close()
        out[flag] = Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    with capsys.disabled():
        for flag, r in out.items():
            subs = r["subcategory_scores"]
            print(f"\n  Project-strong grad, NO_ROLES_RELEVANCE_SCOPE_NA={flag}: final {r['final']:.1f}, "
                  f"experience {r['dimension_scores']['experience']:.2f} "
                  f"(relevance {subs['relevance']['status']}, scope {subs['scope']['status']})")
    assert out[False]["subcategory_scores"]["relevance"]["status"] == "missing"
    assert out[True]["subcategory_scores"]["relevance"]["status"] == "na"
    assert out[True]["subcategory_scores"]["scope"]["status"] == "na"
    assert out[True]["dimension_scores"]["experience"] > out[False]["dimension_scores"]["experience"]
