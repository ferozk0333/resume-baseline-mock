"""Scoring, the run view, weights, the computation view and the improvement plan."""
from __future__ import annotations

import json

from fastapi import HTTPException

from api.context import Api
from api.schemas import ScoreIn, TaskOut, WeightsIn
from core import config, db
from extraction import store
from extraction.records import scoring_blocker
from graph.pipeline import current_plan, regenerate_plan, rescore, run_gaps, run_scores, save_run_weights
from judges.service import clear_judgments
from scoring.explain import FORMULAS
from scoring.plan import TYPE_LABELS


def register(api: Api) -> None:
    app, conn, not_found, state, tasks = api.app, api.conn, api.not_found, api.state, api.tasks
    run_row, start_scoring = api.run_row, api.start_scoring

    # --- Scoring ---------------------------------------------------------------------------------------
    @app.post("/candidates/{candidate_id}/score", response_model=TaskOut, status_code=202, tags=["scoring"])
    def score(candidate_id: str, body: ScoreIn):
        with conn() as c:
            store.get_candidate(c, candidate_id) or not_found("Candidate")
            if db.get_baseline(c, body.baseline_id) is None:
                not_found("Baseline")
            blocker = scoring_blocker(store.load_profile(c, candidate_id))
            from market.baseline import resolve_baseline
            baseline_id = resolve_baseline(c, body.baseline_id)     # Manual or Market (specs/15 §9)
        if blocker:
            raise HTTPException(409, blocker)
        return {"task_id": start_scoring(candidate_id, baseline_id, body.weights)}

    def run_view(c, run: dict, weights: dict) -> dict:
        scores = run_scores(c, run["id"])
        agg = rescore(c, run["id"], weights)
        subs = []
        for dim, names in config.SUBCATEGORIES.items():
            for s in names:
                # Not saved yet: still being judged while the run is running; an error once it has stopped.
                absent = "pending" if run["status"] == "running" else "error"
                v = scores.get(s, {"status": absent, "score": None, "details": {}, "guidance": None})
                subs.append({"key": s, "label": config.SUBCATEGORY_LABELS[s], "dimension": dim,
                             "status": v["status"], "score": v["score"], "guidance": v["guidance"],
                             "details": v["details"], "formula": FORMULAS.get(s),
                             "weight": weights["subcategories"][dim][s],
                             "effective_weight": agg.effective_weights.get(s, 0.0)})
        b = c.execute("SELECT market_version_id, title FROM baselines WHERE id = ?", (run["baseline_id"],)).fetchone()
        source = "manual"
        if b and b["market_version_id"]:
            v = c.execute("SELECT version FROM market_versions WHERE id = ?", (b["market_version_id"],)).fetchone()
            source = f"market v{v[0]}" if v else "market"
        return {"run": {k: run[k] for k in ("id", "candidate_id", "baseline_id", "status", "started_at",
                                            "finished_at", "logic_version", "plan_status")} | {"baseline_source": source},
                "progress": {"done": sum(1 for x in subs if x["status"] != "pending"), "total": len(subs)},
                "weights": weights, "final": agg.final, "dimensions": agg.dimensions, "subcategories": subs,
                "gaps": [g.as_dict() for g in run_gaps(c, run["id"], weights)], "audit": config.SCORE_AUDIT}

    @app.get("/runs/{run_id}", tags=["scoring"])
    def get_run(run_id: str):
        with conn() as c:
            run = run_row(c, run_id)
            return run_view(c, run, json.loads(run["weights_json"]))

    @app.get("/runs/{run_id}/benchmarks", tags=["scoring"])
    def run_benchmarks(run_id: str):
        """Percentiles and "what higher scorers have" (specs/12). Recomputed from saved runs on every request."""
        from scoring import benchmarks
        with conn() as c:
            run_row(c, run_id)
            return benchmarks.for_run(c, run_id)

    @app.post("/runs/{run_id}/rescore", tags=["scoring"])
    def rescore_run(run_id: str, body: WeightsIn):
        """Re-aggregate saved scores and re-rank gaps with other weights. No model calls."""
        with conn() as c:
            run = run_row(c, run_id)
            try:
                return run_view(c, run, body.weights)
            except (KeyError, TypeError) as exc:
                raise HTTPException(422, f"weights are incomplete: missing {exc}") from exc

    def computation_view(run_id: str, weights: dict | None):
        """How each score was computed (SCORE_AUDIT): read-only, from saved data, no model call."""
        if not config.SCORE_AUDIT:
            raise HTTPException(404, "The computation view is turned off (SCORE_AUDIT=0).")
        from graph.audit import computation
        with conn() as c:
            run_row(c, run_id)
        try:
            return computation(state.db_path, run_id, weights)
        except (KeyError, TypeError) as exc:
            raise HTTPException(422, f"weights are incomplete: missing {exc}") from exc

    @app.get("/runs/{run_id}/computation", tags=["scoring"])
    def get_computation(run_id: str):
        return computation_view(run_id, None)

    @app.post("/runs/{run_id}/computation", tags=["scoring"])
    def post_computation(run_id: str, body: WeightsIn):
        """The same with the weights on the sliders (dimension and final arithmetic follow them)."""
        return computation_view(run_id, body.weights)

    @app.put("/runs/{run_id}/weights", tags=["scoring"])
    def save_weights(run_id: str, body: WeightsIn):
        with conn() as c:
            run_row(c, run_id)
            save_run_weights(c, run_id, body.weights)
        return {"saved": True}

    def plan_view(plan) -> dict:
        return {"status": "failed" if plan.error else "ready", "error": plan.error, "markdown": plan.markdown,
                "model": plan.model_version, "quick_wins": plan.quick_wins, "not_actionable": plan.not_actionable,
                "gaps": [g.as_dict() for g in plan.gaps], "dropped": plan.dropped,
                "actions": [{**p.action.model_dump(), "type_label": TYPE_LABELS.get(p.action.type, p.action.type),
                             "points": round(p.points, 1), "points_text": p.points_text}
                            for p in plan.actions]}

    @app.get("/runs/{run_id}/plan", tags=["plan"])
    def get_plan(run_id: str, weights: str | None = None):
        with conn() as c:
            run = run_row(c, run_id)
            w = json.loads(weights) if weights else None
            plan = current_plan(c, run_id, w)
            if plan is None:
                return {"status": run.get("plan_status") or "none", "actions": [], "gaps": []}
            return plan_view(plan)

    @app.post("/runs/{run_id}/plan", tags=["plan"])
    def open_plan(run_id: str):
        """First open of a role's plan (specs/17 §3.1): write it now if it was deferred. {"status": "ready"} when it
        already exists, else {"task_id"} ("Preparing your plan…"). One plan call, recorded on the run."""
        with conn() as c:
            run = run_row(c, run_id)
            if current_plan(c, run_id) is not None:
                return {"status": "ready", "task_id": None}
            if run.get("plan_status") == "generating":
                return {"status": "generating", "task_id": None}
            db.set_plan_status(c, run_id, "generating")
        task_id = tasks.create("plan", run["candidate_id"], run_id)

        def work(progress):
            progress.say("gap_analysis", "Preparing your plan…", 20)
            from graph.pipeline import write_plan
            from graph.pipeline import run_scores as scores_of
            with conn() as c:
                subs = scores_of(c, run_id)
            plan = write_plan(state.deps, run_id, run["candidate_id"], run["baseline_id"], subs,
                              json.loads(run["weights_json"]))
            if plan.error:
                raise ValueError(f"The plan could not be prepared: {plan.error}")
            return {"run_id": run_id, "_message": "Plan ready"}
        tasks.submit(task_id, work)
        return {"status": "generating", "task_id": task_id}

    @app.post("/runs/{run_id}/plan/regenerate", response_model=TaskOut, status_code=202, tags=["plan"])
    def regenerate(run_id: str, body: WeightsIn | None = None):
        with conn() as c:
            run = run_row(c, run_id)
        task_id = tasks.create("plan", run["candidate_id"], run_id)

        def work(progress):
            progress.say("gap_analysis", "Writing a new improvement plan…", 20)
            with conn() as c:
                db.set_plan_status(c, run_id, "generating")
                plan = regenerate_plan(c, run_id, state.deps.planner, body.weights if body else None)
                db.set_plan_status(c, run_id, "failed" if plan.error else "ready")
            if plan.error:
                raise ValueError(f"The plan could not be generated: {plan.error}")
            return {"run_id": run_id, "_message": "Plan ready"}
        tasks.submit(task_id, work)
        return {"task_id": task_id}

    @app.post("/runs/{run_id}/rejudge", response_model=TaskOut, status_code=202, tags=["scoring"])
    def rejudge(run_id: str):
        with conn() as c:
            run = run_row(c, run_id)
            clear_judgments(c, run["candidate_id"], run["baseline_id"])
        return {"task_id": start_scoring(run["candidate_id"], run["baseline_id"], json.loads(run["weights_json"]),
                                         kind="rejudge")}

