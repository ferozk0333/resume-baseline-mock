"""Baseline role profiles (specs/03, specs/06)."""
from __future__ import annotations

from api.context import Api
from core import db
from core.models import Baseline


def register(api: Api) -> None:
    app, conn, not_found = api.app, api.conn, api.not_found

    # --- Baselines ---------------------------------------------------------------------------------
    @app.get("/baselines", response_model=list[Baseline], tags=["baselines"])
    def list_baselines():
        """Role baselines (a recruiter job's own baseline is managed through the job, never listed here)."""
        with conn() as c:
            return db.list_baselines(c)

    @app.get("/baselines/{baseline_id}", response_model=Baseline, tags=["baselines"])
    def get_baseline(baseline_id: str):
        with conn() as c:
            return db.get_baseline(c, baseline_id) or not_found("Baseline")

    @app.post("/baselines", response_model=Baseline, status_code=201, tags=["baselines"])
    def create_baseline(baseline: Baseline):
        with conn() as c:
            new_id = db.create_baseline(c, baseline.model_copy(update={"id": None}))
            return db.get_baseline(c, new_id)

    @app.put("/baselines/{baseline_id}", response_model=Baseline, tags=["baselines"])
    def update_baseline(baseline_id: str, baseline: Baseline):
        with conn() as c:
            if db.get_baseline(c, baseline_id) is None:
                not_found("Baseline")
            db.update_baseline(c, baseline.model_copy(update={"id": baseline_id}))
            return db.get_baseline(c, baseline_id)

    @app.delete("/baselines/{baseline_id}", status_code=204, tags=["baselines"])
    def delete_baseline(baseline_id: str):
        with conn() as c:
            if not db.delete_baseline(c, baseline_id):
                not_found("Baseline")

    @app.get("/baselines-check", tags=["baselines"])
    def check_level(level: str, required_years: float):
        """Level/years warning (specs/06): shown as the user types."""
        from core.models import level_years_warning
        return {"warning": level_years_warning(level, required_years)}

    @app.post("/baselines/reset-seed", response_model=list[str], tags=["baselines"])
    def reset_seed():
        with conn() as c:
            return db.seed_baselines(c, reset=True)

