"""Candidates: create, onboard (upload and read), the profile and its edits, runs, files."""
from __future__ import annotations

import json
import os
from typing import Literal

from fastapi import File, Form, HTTPException, UploadFile

from api.context import Api
from api.schemas import CandidateIn, CandidateOut, ProfileEdit, TaskOut
from core import db
from extraction import store
from extraction.onboard import CertificationForm, OnboardingInputs, ReferenceForm, Upload
from extraction.records import scoring_blocker
from services import media


def register(api: Api) -> None:
    app, conn, not_found = api.app, api.conn, api.not_found
    read_upload, state, tasks = api.read_upload, api.state, api.tasks
    prejudge_targets = api.prejudge_targets

    # --- Candidates ----------------------------------------------------------------------------------
    @app.get("/candidates", response_model=list[CandidateOut], tags=["candidates"])
    def list_candidates():
        with conn() as c:
            return store.list_candidates(c)

    @app.post("/candidates", response_model=CandidateOut, status_code=201, tags=["candidates"])
    def create_candidate(body: CandidateIn):
        with conn() as c:
            cid = store.create_candidate(c, body.name, body.target_role)
            return store.get_candidate(c, cid)

    @app.post("/candidates/{candidate_id}/onboard", response_model=TaskOut, status_code=202, tags=["candidates"])
    async def onboard(candidate_id: str, baseline_id: str | None = Form(None), resume: UploadFile = File(...),
                      linkedin_pdf: UploadFile | None = File(None), github: str | None = Form(None),
                      portfolio_url: str | None = Form(None),
                      github_pdf: UploadFile | None = File(None, description="Saved GitHub profile page (PDF); "
                                                                                 "used instead of `github`"),
                      portfolio_pdf: UploadFile | None = File(None, description="Portfolio saved as PDF; used "
                                                                                    "instead of `portfolio_url`"),
                      linkedin_url: str | None = Form(None, description="LinkedIn profile URL (display only)"),
                      demo_videos: str = Form("[]", description='JSON list of YouTube/Loom URLs (max 3)'),
                      certifications: str = Form("[]", description="JSON list of {name, issuer, date, "
                                                                    "credential_id, verify_url}"),
                      references: str = Form("[]", description="JSON list of {name, relationship, contact, "
                                                                "statement}"),
                      onboarding: bool = Form(False, description="First-run onboarding (specs/17): plain-language "
                                                                 "progress; target roles are picked while it runs")):
        """Read the documents and stop at review. Without a baseline (onboarding: roles are picked while this runs),
        the job-independent questions that start during review use the primary target role, or the first role
        baseline: they don't depend on the role, so any role baseline gives the same answers."""
        with conn() as c:
            cand = store.get_candidate(c, candidate_id) or not_found("Candidate")
            if baseline_id is None:
                from services import candidate as cx
                baseline_id = (cx.primary(c, candidate_id) or {}).get("baseline_id") or next(iter(cx.role_baselines(c)))
            if db.get_baseline(c, baseline_id) is None:
                not_found("Baseline")
            from market.baseline import resolve_baseline
            baseline_id = resolve_baseline(c, baseline_id)          # the role's Market baseline when toggled on
        try:
            videos = json.loads(demo_videos or "[]")
            media.validate_links(videos)
            certs = [CertificationForm(**x) for x in json.loads(certifications or "[]")]
            refs = [ReferenceForm(**x) for x in json.loads(references or "[]")]
            from services import linkedin as li
            profile_url = li.normalize(linkedin_url) if (linkedin_url or "").strip() else None
        except media.MediaError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, f"Could not read the form: {exc}") from exc
        inputs = OnboardingInputs(
            resume=Upload(resume.filename or "resume", await read_upload(resume, "Your resume")),
            linkedin_pdf=Upload(linkedin_pdf.filename or "linkedin.pdf",
                                await read_upload(linkedin_pdf, "The LinkedIn PDF"))
            if linkedin_pdf is not None and linkedin_pdf.filename else None,
            github_pdf=Upload(github_pdf.filename or "github.pdf", await read_upload(github_pdf, "The GitHub PDF"))
            if github_pdf is not None and github_pdf.filename else None,
            portfolio_pdf=Upload(portfolio_pdf.filename or "portfolio.pdf",
                                 await read_upload(portfolio_pdf, "The portfolio PDF"))
            if portfolio_pdf is not None and portfolio_pdf.filename else None,
            github=github or None, portfolio_url=portfolio_url or None, certifications=certs, references=refs,
            name=cand["name"], target_role=cand["target_role"], candidate_id=candidate_id)
        task_id = tasks.create("onboard", candidate_id)
        from services import wait_screens as ws               # the reading wait screen lists these (specs/17)
        added = ws.added_sources({"linkedin_pdf": inputs.linkedin_pdf, "github": inputs.github or inputs.github_pdf,
                                  "portfolio": inputs.portfolio_url or inputs.portfolio_pdf})
        tasks.event(task_id, "queued", "Waiting to start", 0, result_json={"sources": added})
        if onboarding:
            from services import candidate as cx
            with conn() as c:
                cx.set_step(c, candidate_id, "roles", read_task_id=task_id, confirmed_at=None)

        def work(progress):
            progress.say("ingest", "Reading your documents…", 2)
            if onboarding:
                from services import candidate as cx
                on_event = cx.reading_progress(progress)
            else:
                on_event = progress.on_event
            res = state.pipeline.start_onboarding(inputs, baseline_id, on_event=on_event)
            with conn() as c:
                media.save_links(c, candidate_id, videos)
                if profile_url:
                    with c:
                        c.execute("UPDATE candidates SET linkedin_url = ? WHERE id = ?", (profile_url, candidate_id))
            if res.get("awaiting_review"):
                state.review_threads[candidate_id] = res["thread_id"]
                if onboarding and not res["awaiting_review"].get("blocked"):
                    prejudge_targets(candidate_id)                 # early start for the roles already picked
                return {"_status": "done", "_message": "Ready for review", "candidate_id": candidate_id, "sources": added,
                        "warnings": res.get("warnings") or [], "blocked": res["awaiting_review"].get("blocked")}
            raise ValueError(res.get("blocked") or "Onboarding stopped before the review step.")
        tasks.submit(task_id, work)
        return {"task_id": task_id}

    def public_warning(e: dict) -> str:
        """A failed source as the candidate sees it on Review: never model or provider details."""
        from services import candidate as cx
        label = {"resume": "Resume", "linkedin_pdf": "LinkedIn PDF", "portfolio": "Portfolio",
                 "github_repo": "GitHub repo"}.get(e["type"], e["type"])
        text = cx.public_error(e["error"])
        return text if text.lower().startswith(label.lower()) else f"{label}: {text}"

    @app.get("/candidates/{candidate_id}/profile", tags=["candidates"])
    def get_profile(candidate_id: str):
        with conn() as c:
            cand = store.get_candidate(c, candidate_id) or not_found("Candidate")
            profile = store.load_profile(c, candidate_id)
            evidence = store.list_evidence(c, candidate_id)
            from dataclasses import asdict
            data = json.loads(json.dumps(asdict(profile), default=str))
            from services.review import review_view
            review_view(data, profile, evidence, store.list_extra(c, candidate_id))
            from services import candidate as cx
            from services import wait_screens as ws
            from extraction.deletions import mentions as deletion_mentions
            from extraction.prompt_guard import notices as guard_notices
            ob = cx.onboarding(c, candidate_id)
            read_task = tasks.get(ob["read_task_id"]) if ob.get("read_task_id") else None
            pending = ws.pending_sources(c, candidate_id, read_task)
            from extraction import skill_groups
            return {
                "candidate": cand,
                "profile": data,
                "skill_groups": skill_groups.groups(),
                # Education, patents, volunteering, courses, languages: shown to recruiters, never scored (specs/19 D).
                "extra": data.pop("extra"),
                # Review opens once the resume is read; these sources are still being read (Confirm waits for them).
                "pending": pending,
                "reading": bool(read_task and read_task["status"] in ("queued", "running")),
                # Deleted in Review, then named again by a source read later: shown with "Add back", never re-added.
                "mentions": deletion_mentions(c, candidate_id),
                # Hidden text that the injection screen also flagged (it stayed removed). Nothing else it finds is shown.
                "notices": guard_notices(c, candidate_id),
                "dropped": store.list_dropped(c, candidate_id),
                "warnings": [public_warning(e) for e in evidence if e["status"] == "failed" and e["error"]],
                "sources": [{"id": e["id"], "type": e["type"], "status": e["status"], "text": e["raw_text"]}
                            for e in evidence if e["type"] in ("resume", "linkedin_pdf", "portfolio")],
                "media_links": media.list_links(c, candidate_id),
                "blocker": scoring_blocker(profile),
            }

    @app.patch("/candidates/{candidate_id}/profile", tags=["candidates"])
    def edit_profile(candidate_id: str, edit: ProfileEdit):
        from datetime import date as _date

        def day(v):
            return _date.fromisoformat(v + "-01" if v and len(v) == 7 else v) if v else None
        d = edit.data
        undo_id = None
        with conn() as c, store.candidate_lock(candidate_id):   # a source may be saved into the profile meanwhile
            store.get_candidate(c, candidate_id) or not_found("Candidate")
            try:
                from extraction import deletions
                if edit.op == "delete" and edit.item == "extra":
                    row = store.delete_extra(c, candidate_id, edit.id or "")
                    if row is None:
                        raise ValueError("Item not found.")
                    undo_id = deletions.remember_extra(c, candidate_id, row)
                elif edit.op == "delete":
                    table = {"role": "roles", "project": "projects", "claim": "claims",
                             "certification": "certifications", "reference": "references_"}.get(edit.item)
                    if table is None or not edit.id:
                        raise ValueError("delete needs an item type and id")
                    undo_id = deletions.remember(c, candidate_id, table, edit.id)   # a later source won't add it back
                    store.delete_item(c, table, edit.id)
                elif edit.op == "undo":
                    deletions.undo(c, candidate_id, edit.id or "")
                elif edit.op == "confirm_dates":
                    store.confirm_dates(c, {"role": "roles", "project": "projects"}.get(edit.item, ""), edit.id or "")
                elif edit.item == "mention":
                    deletions.add_back(c, candidate_id, edit.id or "")
                elif edit.op == "add_back" or edit.item == "dropped":
                    store.restore_dropped(c, edit.id)
                elif edit.item == "role":
                    store.save_role(c, candidate_id, role_id=edit.id if edit.op == "edit" else None,
                                    title=d.get("title", ""), employer=d.get("employer"), industry=d.get("industry"),
                                    start_date=day(d.get("start_date")), end_date=day(d.get("end_date")),
                                    is_current=bool(d.get("is_current")), team_size=d.get("team_size"),
                                    bullets=d.get("bullets", []), location=d.get("location"))
                elif edit.item == "project":
                    store.save_project(c, candidate_id, project_id=edit.id if edit.op == "edit" else None,
                                       kind=d.get("kind", "project"), name=d.get("name", ""),
                                       description=d.get("description"), url=d.get("url"),
                                       start_date=day(d.get("start_date")), end_date=day(d.get("end_date")),
                                       bullets=d.get("bullets", []), role_id=d.get("role_id"),
                                       details=d.get("details"))
                elif edit.item == "claim":
                    store.add_claim(c, candidate_id, d.get("kind", "skill"), d.get("name", ""),
                                    skill_group=d.get("group"), details=d.get("details"),
                                    claim_id=edit.id if edit.op == "edit" else None)
                elif edit.item == "certification":
                    store.save_certification(c, candidate_id, cert_id=edit.id if edit.op == "edit" else None,
                                             name=d.get("name", ""), issuer=d.get("issuer"),
                                             issued_date=day(d.get("issued_date")),
                                             credential_id=d.get("credential_id"), verify_url=d.get("verify_url"))
                elif edit.item == "reference":
                    store.save_reference(c, candidate_id, ref_id=edit.id if edit.op == "edit" else None,
                                         name=d.get("name", ""), relationship=d.get("relationship"),
                                         contact=d.get("contact"), statement=d.get("statement"))
                elif edit.item == "extra":
                    store.save_extra(c, candidate_id, d.get("type", ""), d.get("fields") or {},
                                     item_id=edit.id if edit.op == "edit" else None)
                else:
                    raise ValueError(f"{edit.op} {edit.item} is not supported")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return {**get_profile(candidate_id), "undo": undo_id}

    @app.get("/candidates/{candidate_id}/runs", tags=["candidates"])
    def candidate_runs(candidate_id: str):
        with conn() as c:
            return db.list_runs(c, candidate_id)

    @app.get("/candidates/{candidate_id}/files/{kind}", tags=["candidates"])
    def candidate_file(candidate_id: str, kind: Literal["resume", "linkedin"]):
        """The uploaded (or rendered demo) resume or LinkedIn PDF, for the recruiter's inline viewer."""
        from fastapi.responses import FileResponse
        etype = {"resume": "resume", "linkedin": "linkedin_pdf"}[kind]
        with conn() as c:
            ev = next((e for e in store.list_evidence(c, candidate_id) if e["type"] == etype and e["file_path"]), None)
        if ev is None or not os.path.exists(ev["file_path"]):
            not_found("Resume file" if kind == "resume" else "LinkedIn PDF")
        ext = ev["file_path"].rsplit(".", 1)[-1].lower()
        media = "application/pdf" if ext == "pdf" else \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return FileResponse(ev["file_path"], media_type=media,
                            headers={"Content-Disposition": f'inline; filename="{kind}.{ext}"'})

