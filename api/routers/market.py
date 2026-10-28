"""Market baselines (specs/15)."""
from __future__ import annotations

import asyncio
import json

from fastapi import File, Form, HTTPException, UploadFile

from api.context import Api
from api.schemas import DecideIn, PostingUrlIn, RecomputeIn, SourceIn, SwitchBaselineIn
from core import config
from market import baseline as mb
from market import normalize as mnorm
from market import pipeline as mp
from market import sources as msrc
from market import store as mstore


def register(api: Api) -> None:
    app, conn, not_found, read_upload, state = api.app, api.conn, api.not_found, api.read_upload, api.state
    run_row, start_scoring = api.run_row, api.start_scoring
    market_call = api.call

    # --- Market baselines (specs/15) -------------------------------------------------------------------------
    def role_or_404(key: str) -> msrc.Role:
        return msrc.load().get(key) or not_found("Role")

    @app.get("/market/roles", tags=["market"])
    def market_roles():
        with conn() as c:
            out = []
            for key, r in msrc.load().items():
                rows = mstore.postings(c, key)
                vs = mb.versions(c, key)
                out.append({"key": key, "title": r.title, "manual_baseline": r.manual_baseline,
                            "source": mb.source_of(c, key), "active": next((v for v in vs if v["status"] == "published"), None),
                            "latest": vs[0] if vs else None, "postings": len(rows),
                            "levels": mstore.level_counts(c, key),
                            "companies": len({p["company"] for p in rows}),
                            "configured": {"companies": len(r.companies),
                                           "placeholders": sum(c2.placeholder for c2 in r.companies),
                                           "fetchable": sum(1 for c2 in r.companies if not c2.placeholder and
                                                            c2.source != "manual" and c2.source_id)}})
            return {"roles": out,
                    "thresholds": {"WINDOW_DAYS": config.WINDOW_DAYS, "REQ_SHARE": config.REQ_SHARE,
                                   "PREF_SHARE": config.PREF_SHARE, "MARGIN": config.MARGIN}}

    @app.get("/market/{role}/versions", tags=["market"])
    def market_versions(role: str):
        role_or_404(role)
        with conn() as c:
            return [{k: v for k, v in x.items() if k != "content"} for x in mb.versions(c, role)]

    @app.get("/market/{role}/postings", tags=["market"])
    def market_postings(role: str):
        role_or_404(role)
        with conn() as c:
            return [{k: p[k] for k in ("id", "company", "tier", "source", "title", "level", "level_method", "source_url",
                                       "first_seen", "last_seen", "posted_at")} | {"extracted": mstore.cached(c, p["text_hash"]) is not None}
                    for p in mstore.postings(c, role)]

    @app.post("/market/{role}/postings", tags=["market"])
    async def market_add(role: str, company: str = Form(...), tier: str = Form(...), title: str = Form(...),
                         url: str | None = Form(None), posted_at: str | None = Form(None), text: str | None = Form(None),
                         file: UploadFile | None = File(None)):
        """Add postings by hand (companies without a public API, and the first test postings)."""
        r = role_or_404(role)
        from ingest.documents import DocumentError, extract_text
        try:
            if file is not None and file.filename:
                data = await read_upload(file, "The file")
                with conn() as c:
                    text = extract_text(file.filename, data, c)
        except DocumentError as exc:
            raise HTTPException(422, str(exc)) from exc
        with conn() as c:
            return market_call(mp.add_manual, c, r, company=company, tier=tier, title=title, text=text or "", url=url,
                               posted_at=posted_at)

    @app.post("/market/{role}/postings/from-url", tags=["market"])
    def market_from_url(role: str, body: PostingUrlIn):
        """Read a posting's page to fill the Add form (job-board APIs, schema.org, else the page text). Code only, no
        model call, nothing saved: the recruiter checks the fields and adds the posting."""
        from market import from_url
        r = role_or_404(role)
        try:
            got = from_url.read(body.url)
        except from_url.UrlError as exc:
            raise HTTPException(422, str(exc)) from exc
        known = next((c for c in r.companies if not c.placeholder and c.name.lower() == got["company"].lower()), None)
        return {**got, "tier": known.tier if known else None, "title_matches": r.title_matches(got["title"]),
                "role_title": r.title}

    @app.post("/market/{role}/fetch", tags=["market"])
    def market_fetch(role: str):
        """Read the role's public job boards now (normally weekly). No model calls."""
        r = role_or_404(role)
        with conn() as c:
            return mp.fetch_role(c, r)

    @app.post("/market/{role}/recompute", tags=["market"])
    async def market_recompute(role: str, body: RecomputeIn | None = None):
        """A new draft version over the window: extraction of new postings (cached), aggregation, one summary."""
        r = role_or_404(role)

        def work():
            with conn() as c:
                return mb.recompute(c, r, llm=state.job_llm, test=body.test if body else None)
        return await asyncio.to_thread(work)

    @app.post("/market/{role}/fixtures", tags=["market"])
    def market_fixtures(role: str):
        """Test mode: load the hand-labelled fixture postings for the role (no model)."""
        r = role_or_404(role)
        with conn() as c:
            return {"added": mp.load_fixtures(c, r)}

    @app.get("/market/versions/{vid}", tags=["market"])
    def market_version(vid: str):
        with conn() as c:
            v = mb.get_version(c, vid) or not_found("Version")
            r = msrc.load()[v["role"]]
            return {**v, "diff": mb.diff(c, vid), "side_by_side": mb.side_by_side(c, r, vid), "role_title": r.title,
                    "can_publish": v["status"] == "draft"}

    @app.post("/market/versions/{vid}/publish", tags=["market"])
    def market_publish(vid: str):
        """Nothing goes live without a person publishing it."""
        with conn() as c:
            mb.get_version(c, vid) or not_found("Version")
            return market_call(mb.publish, c, vid)

    @app.post("/market/versions/{vid}/discard", tags=["market"])
    def market_discard(vid: str):
        with conn() as c:
            return mb.discard(c, vid)

    @app.get("/market/suggestions", tags=["market"])
    def market_suggestions(status: str | None = "pending"):
        with conn() as c:
            return {"suggestions": mnorm.suggestions(c, status),
                    "canonical": sorted(mnorm.canonicals(mnorm.load_map()))}

    @app.post("/market/suggestions/{sid}", tags=["market"])
    def market_decide(sid: str, body: DecideIn):
        """A person approves (merge into the canonical name; written to the reviewed map) or rejects."""
        with conn() as c:
            return market_call(mnorm.decide, c, sid, body.approve, body.canonical)

    @app.post("/market/{role}/source", tags=["market"])
    def market_source(role: str, body: SourceIn):
        """The toggle. 409 with "Switching will re-score N candidates (about M model calls)" unless confirmed."""
        r = role_or_404(role)
        with conn() as c:
            est = mb.switch_estimate(c, r, body.source)
            if not est["possible"]:
                raise HTTPException(422, est["reason"])
            if est["candidates"] and not body.confirm:
                raise HTTPException(409, est)
            mb.set_setting(c, f"source:{role}", body.source)
            return {"source": body.source, **est}

    @app.get("/runs/{run_id}/baselines", tags=["market"])
    def run_baselines(run_id: str):
        """The Score page's "Scored against: Standard | Market (vN, date)" switch."""
        with conn() as c:
            return mb.run_options(c, run_row(c, run_id))

    @app.post("/runs/{run_id}/switch-baseline", tags=["market"])
    def switch_baseline(run_id: str, body: SwitchBaselineIn):
        """Re-score this candidate against the other baseline. 409 with the re-score notice unless confirmed."""
        with conn() as c:
            run = run_row(c, run_id)
            opts = mb.run_options(c, run)
            target = (opts.get(body.source) or {}).get("baseline_id")
            if target is None:
                raise HTTPException(422, "No published Market baseline for this role yet.")
            if target == run["baseline_id"]:
                raise HTTPException(422, "This score already uses that baseline.")
            est = mb.candidate_switch_estimate(c, run["candidate_id"], run["baseline_id"], target)
            if not body.confirm:
                label = "the Standard baseline" if body.source == "standard" else opts["market"]["label"]
                raise HTTPException(409, {"target": label, **est})
        task_id = start_scoring(run["candidate_id"], target, json.loads(run["weights_json"]), kind="rescore")
        return {"task_id": task_id}

    @app.get("/market-view", tags=["market"])
    def market_view_roles():
        """Candidate side: roles with a published Market version (counts only)."""
        with conn() as c:
            return [{"role": k, "title": r.title, "available": mb.active(c, k) is not None} for k, r in msrc.load().items()]

    @app.get("/market-view/{role}", tags=["market"])
    def market_view(role: str):
        r = role_or_404(role)
        with conn() as c:
            return mb.market_view(c, r) or not_found("Published Market version")

    @app.get("/market/dry-run", tags=["market"])
    def market_dry_run():
        with conn() as c:
            return mp.dry_run(c, msrc.load())

