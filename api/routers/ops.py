"""Health, upload settings and the app's mode and default weights."""
from __future__ import annotations

import os

from fastapi.responses import PlainTextResponse

from api.context import Api
from core import config


def register(api: Api) -> None:
    app, conn = api.app, api.conn

    @app.get("/healthz", tags=["ops"], response_class=PlainTextResponse)
    def healthz():
        """Liveness for the proxy and Docker: the app answers and its database opens."""
        with conn() as c:
            c.execute("SELECT 1").fetchone()
        return "ok"

    @app.get("/settings/uploads", tags=["candidates"])
    def upload_settings():
        """What the upload drop zones accept (the size limit lives in config)."""
        return {"max_mb": config.UPLOAD_MAX_MB, "max_bytes": int(config.UPLOAD_MAX_MB * 1024 * 1024)}

    @app.get("/config/mode", tags=["scoring"])
    def mode():
        """"demo": the API runs on fakes (scripted extraction and labels; uploads are not read). "live": real models."""
        fake = bool(os.getenv("RIGHTSTAFF_DEPS_FACTORY"))
        return {"mode": "demo" if fake else "live", "judge": config.JUDGE,
                "models": None if fake else {"extract": config.EXTRACT_MODEL, "judge_fallback": config.JUDGE_FALLBACK_MODEL,
                                             "complexity": config.COMPLEXITY_MODEL, "plan": config.REASONING_MODEL}}

    @app.get("/config/weights", tags=["scoring"])
    def default_weights():
        return {"weights": config.default_weights(), "labels": config.SUBCATEGORY_LABELS}

