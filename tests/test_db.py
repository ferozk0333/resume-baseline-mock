"""Phase 1: schema, seed loading and baseline CRUD."""
import pytest
from pydantic import ValidationError

from core import config, db
from core.models import Baseline, BaselineCriterion

EXPECTED_TABLES = {
    "baselines", "baseline_criteria", "candidates", "evidence_items", "roles", "projects",
    "bullets", "claims", "certifications", "references_", "runs", "judgments", "scores",
    "gap_reports",
}

# (skills req, skills pref, tools req, tools pref, certifications) per seed baseline
SEED_COUNTS = {
    "baseline-ai-engineer-entry": (6, 2, 3, 4, 1),
    "baseline-data-scientist-entry": (6, 2, 4, 3, 1),
    "baseline-product-manager-entry": (5, 3, 2, 2, 1),
    "baseline-software-engineer-entry": (4, 3, 3, 3, 1),
    "baseline-business-analyst-entry": (4, 2, 2, 3, 1),
    "baseline-data-analyst-entry": (5, 2, 3, 2, 1),
}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def seeded(tmp_path):
    c = db.init_app_db(tmp_path / "test.db")
    yield c
    c.close()


def _sample(**overrides) -> Baseline:
    data = dict(
        title="ML Ops Engineer",
        level="mid",
        required_years=3,
        expected_reach=3,
        scope_expectation="Owns the deployment pipeline.",
        complexity_criteria="Multi-service deployments.",
        description="Runs models in production.",
        is_mock=False,
        criteria=[
            BaselineCriterion(kind="skill", name="Monitoring", aliases=["observability", " "], priority="required"),
            BaselineCriterion(kind="tool", name="Kubernetes", aliases=["k8s"], priority="preferred"),
        ],
    )
    data.update(overrides)
    return Baseline(**data)


# --- Schema ---------------------------------------------------------------

def test_schema_creates_all_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert EXPECTED_TABLES <= names


def test_schema_indexes_and_version(conn):
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_judgments_lookup", "idx_scores_run"} <= idx
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "x.db"
    db.connect(path).close()
    c = db.connect(path)  # second open must not fail or recreate
    assert EXPECTED_TABLES <= {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()


# --- Seed -----------------------------------------------------------------

def test_seed_loads_six_baselines_with_criteria(seeded):
    baselines = {b.id: b for b in db.list_baselines(seeded)}
    assert set(baselines) == set(SEED_COUNTS)
    for bid, (sr, sp, tr, tp, certs) in SEED_COUNTS.items():
        b = baselines[bid]
        assert b.is_mock and b.level == "entry" and b.required_years == 1
        assert len(b.items("skill", "required")) == sr
        assert len(b.items("skill", "preferred")) == sp
        assert len(b.items("tool", "required")) == tr
        assert len(b.items("tool", "preferred")) == tp
        assert len(b.items("certification")) == certs


def test_seed_preserves_fields_and_aliases(seeded):
    pm = db.get_baseline(seeded, "baseline-product-manager-entry")
    assert pm.title == "Product Manager" and pm.expected_reach == 3
    assert pm.scope_expectation.startswith("Owns a feature")
    jira = next(c for c in pm.criteria if c.name == "Jira or Linear")
    assert jira.kind == "tool" and jira.priority == "required"
    assert jira.aliases == ["Jira", "Linear", "Asana"]
    # criteria come back in the order of the seed file
    assert pm.criteria[0].name == "Product discovery and user research"


def test_seed_is_idempotent_and_ids_are_stable(seeded):
    before = {c.id for b in db.list_baselines(seeded) for c in b.criteria}
    assert db.seed_baselines(seeded) == []  # nothing new written
    assert len(db.list_baselines(seeded)) == 6
    db.seed_baselines(seeded, reset=True)
    after = {c.id for b in db.list_baselines(seeded) for c in b.criteria}
    assert before == after
    assert seeded.execute("SELECT COUNT(*) FROM baseline_criteria").fetchone()[0] == 16 + 16 + 13 + 14 + 12 + 13


def test_reset_restores_seed_and_keeps_user_baselines(seeded):
    ds = db.get_baseline(seeded, "baseline-data-scientist-entry")
    db.update_baseline(seeded, ds.model_copy(update={"title": "Edited", "criteria": []}))
    user_id = db.create_baseline(seeded, _sample())

    db.seed_baselines(seeded, reset=True)

    restored = db.get_baseline(seeded, "baseline-data-scientist-entry")
    assert restored.title == "Data Scientist" and len(restored.criteria) == 16
    assert db.get_baseline(seeded, user_id) is not None


def test_deleted_seed_baseline_stays_deleted_on_reopen(tmp_path):
    path = tmp_path / "test.db"
    c = db.init_app_db(path)
    db.delete_baseline(c, "baseline-ai-engineer-entry")
    c.close()
    c = db.init_app_db(path)
    assert db.get_baseline(c, "baseline-ai-engineer-entry") is None
    assert len(db.list_baselines(c)) == 5
    c.close()


# --- CRUD -----------------------------------------------------------------

def test_create_and_get_roundtrip(conn):
    bid = db.create_baseline(conn, _sample())
    b = db.get_baseline(conn, bid)
    assert b.title == "ML Ops Engineer" and b.required_years == 3 and b.is_mock is False
    assert b.updated_at
    assert [(c.kind, c.name, c.priority) for c in b.criteria] == [
        ("skill", "Monitoring", "required"),
        ("tool", "Kubernetes", "preferred"),
    ]
    assert b.criteria[0].aliases == ["observability"]  # blank alias dropped
    assert all(c.id for c in b.criteria)


def test_get_missing_returns_none(conn):
    assert db.get_baseline(conn, "nope") is None


def test_update_replaces_fields_and_criteria(conn):
    bid = db.create_baseline(conn, _sample())
    b = db.get_baseline(conn, bid)
    kept = b.criteria[0]
    new = BaselineCriterion(kind="tool", name="Terraform", priority="required")
    db.update_baseline(conn, b.model_copy(update={"required_years": 0, "criteria": [kept, new]}))

    after = db.get_baseline(conn, bid)
    assert after.required_years == 0
    assert [c.name for c in after.criteria] == ["Monitoring", "Terraform"]
    assert after.criteria[0].id == kept.id  # existing criterion keeps its id
    assert conn.execute("SELECT COUNT(*) FROM baseline_criteria WHERE baseline_id=?", (bid,)).fetchone()[0] == 2


def test_update_unknown_baseline_raises(conn):
    with pytest.raises(KeyError):
        db.update_baseline(conn, _sample(id="missing"))


def test_delete_cascades_criteria(conn):
    bid = db.create_baseline(conn, _sample())
    assert db.delete_baseline(conn, bid) is True
    assert db.get_baseline(conn, bid) is None
    assert conn.execute("SELECT COUNT(*) FROM baseline_criteria WHERE baseline_id=?", (bid,)).fetchone()[0] == 0
    assert db.delete_baseline(conn, bid) is False


# --- Validation -----------------------------------------------------------

@pytest.mark.parametrize("field,value", [("expected_reach", 0), ("expected_reach", 6), ("required_years", -1)])
def test_baseline_field_bounds(field, value):
    with pytest.raises(ValidationError):
        _sample(**{field: value})


def test_criterion_cannot_be_skill_and_tool():
    with pytest.raises(ValidationError, match="both a skill and a tool"):
        _sample(criteria=[
            BaselineCriterion(kind="skill", name="SQL", priority="required"),
            BaselineCriterion(kind="tool", name="sql", priority="preferred"),
        ])


def test_duplicate_criterion_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        _sample(criteria=[
            BaselineCriterion(kind="tool", name="Git", priority="required"),
            BaselineCriterion(kind="tool", name="git", priority="preferred"),
        ])


@pytest.mark.parametrize("bad", [{"kind": "language"}, {"priority": "optional"}, {"name": "  "}])
def test_criterion_field_validation(bad):
    data = {"kind": "skill", "name": "SQL", "priority": "required", **bad}
    with pytest.raises(ValidationError):
        BaselineCriterion(**data)


# --- Config ---------------------------------------------------------------

def test_default_weights_match_spec_table():
    w = config.default_weights()
    assert w["dimensions"] == {"experience": 30, "expertise": 30, "authority": 20, "trust": 20}
    for dim in ("experience", "expertise", "authority"):
        assert sum(w["subcategories"][dim].values()) == pytest.approx(100)
    trust = w["subcategories"]["trust"]
    assert sum(trust.values()) == pytest.approx(100)  # Integrity's 5% redistributed
    assert trust["proof_density"] == pytest.approx(31.58, abs=0.01)
    assert trust["consistency"] == pytest.approx(26.32, abs=0.01)
    assert trust["evidence_quality"] == pytest.approx(10.53, abs=0.01)
    assert sum(len(s) for s in config.SUBCATEGORIES.values()) == 19
    assert set(config.SUBCATEGORY_LABELS) == set(config.SUBCATEGORY_DIMENSION)


def test_default_weights_returns_independent_copy():
    w = config.default_weights()
    w["dimensions"]["trust"] = 0
    assert config.default_weights()["dimensions"]["trust"] == 20


def test_today_override(monkeypatch):
    monkeypatch.setenv("RIGHTSTAFF_TODAY", "2026-01-31")
    assert config.today().isoformat() == "2026-01-31"


# --- Baseline hash (judgment cache key) ----------------------------------

def test_baseline_hash_changes_only_with_judge_visible_fields():
    base = _sample()
    h = base.content_hash()
    assert len(h) == 64 and h == _sample().content_hash()
    # fields the judges never see do not change the hash
    for unseen in ({"required_years": 9}, {"expected_reach": 5}, {"is_mock": True}, {"id": "x"}):
        assert base.model_copy(update=unseen).content_hash() == h
    flipped = [c.model_copy(update={"priority": "preferred"}) for c in base.criteria]
    assert base.model_copy(update={"criteria": flipped}).content_hash() == h
    # reordering criteria does not invalidate cached judgments
    assert base.model_copy(update={"criteria": base.criteria[::-1]}).content_hash() == h
    # fields the judges see do change it
    for seen in ({"title": "X"}, {"description": "X"}, {"scope_expectation": "X"},
                 {"complexity_criteria": "X"}, {"level": "senior"}):
        assert base.model_copy(update=seen).content_hash() != h
    c0 = base.criteria[0]
    for change in ({"name": "Alerting"}, {"description": "new rubric"}, {"aliases": ["obs"]}):
        crit = [c0.model_copy(update=change), *base.criteria[1:]]
        assert base.model_copy(update={"criteria": crit}).content_hash() != h


def test_baseline_hash_survives_db_roundtrip(conn):
    b = _sample()
    bid = db.create_baseline(conn, b)
    assert db.get_baseline(conn, bid).content_hash() == b.content_hash()


def test_judgments_table_has_baseline_hash(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(judgments)")}
    assert "baseline_hash" in cols


def test_migrates_v1_db_to_latest(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    from pathlib import Path
    v1_schema = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()
    old.executescript(v1_schema)
    old.execute("INSERT INTO judgments (id, question, label) VALUES ('j1', 'relevance', 'relevant')")
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()

    c = db.connect(path)
    def cols(table):
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    assert "baseline_hash" in cols("judgments")
    assert "review_reasons" in cols("roles") and "review_reasons" in cols("projects")
    assert {"extracted_json", "items_extracted", "items_dropped"} <= cols("evidence_items")
    assert all("source" in cols(t) for t in ("bullets", "claims", "certifications"))
    assert "restored_id" in cols("dropped_items")
    assert {"used", "confidence", "score", "input_hash"} <= cols("judgments")
    assert {"logic_version", "baseline_hash", "models_json"} <= cols("runs")
    assert c.execute("SELECT label FROM judgments WHERE id='j1'").fetchone()[0] == "relevant"
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    c.close()


# --- Level vs required years warning --------------------------------------

@pytest.mark.parametrize("level,years,warns", [
    ("entry", 1, False), ("entry", 2, False), ("entry", 3, True), ("Entry", 2.5, True),
    ("mid", 3, False), ("senior", 5, False), ("mid", 2, True), ("senior", 0, True),
])
def test_level_years_warning(level, years, warns):
    from core.models import level_years_warning
    assert (level_years_warning(level, years) is not None) is warns


def test_seed_baselines_have_no_level_warning(seeded):
    from core.models import level_years_warning
    assert all(level_years_warning(b.level, b.required_years) is None for b in db.list_baselines(seeded))


# --- Runs -----------------------------------------------------------------

def test_runs_for_deleted_baseline_still_listed(seeded):
    bid = db.create_baseline(seeded, _sample())
    run_live = db.create_run(seeded, "cand-1", "baseline-data-scientist-entry", config.default_weights())
    run_gone = db.create_run(seeded, "cand-1", bid, config.default_weights())
    db.finish_run(seeded, run_gone)
    db.delete_baseline(seeded, bid)

    runs = {r["id"]: r for r in db.list_runs(seeded, "cand-1")}
    assert set(runs) == {run_live, run_gone}
    assert runs[run_live]["baseline_title"] == "Data Scientist" and not runs[run_live]["baseline_deleted"]
    assert runs[run_gone]["baseline_title"] == "(deleted)" and runs[run_gone]["baseline_deleted"]
    assert runs[run_gone]["status"] == "done" and runs[run_gone]["finished_at"]
    assert runs[run_gone]["weights"] == config.default_weights()
    assert db.list_runs(seeded, "someone-else") == []
