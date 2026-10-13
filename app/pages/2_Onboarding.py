"""Onboarding: inputs → extraction → review (edit, add back) → confirm & score."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app import services  # noqa: E402
from app.components.graph_view import GraphView, show_last_run  # noqa: E402
from core import db  # noqa: E402
from extraction import store  # noqa: E402
from extraction.onboard import CertificationForm, OnboardingInputs, ReferenceForm, Upload  # noqa: E402
from extraction.records import REVIEW_MESSAGES, scoring_blocker  # noqa: E402

st.set_page_config(page_title="Onboarding · RightStaff", layout="wide")
st.title("Onboarding")

PROJECT_KINDS = ["project", "hackathon", "publication", "case_study", "presentation", "work_sample", "repo"]


def _review_flags(reasons: list[str]) -> None:
    for r in reasons:
        st.warning(REVIEW_MESSAGES.get(r, r), icon="⚠️")


def _quote(q: str | None) -> None:
    if q:
        with st.expander("Source quote"):
            st.caption(q)


def _bullets_text(item) -> str:
    return "\n".join(b.text for b in item.bullets)


def _lines(text: str) -> list[str]:
    return [ln.strip().lstrip("-•").strip() for ln in (text or "").splitlines() if ln.strip()]


# --- Step 1: inputs -------------------------------------------------------------------------------

def step_inputs(conn) -> None:
    baselines = db.list_baselines(conn)
    bids = [b.id for b in baselines]
    cur = st.session_state.get("baseline_id")
    st.session_state.baseline_id = st.selectbox(
        "Baseline to score against", bids, index=bids.index(cur) if cur in bids else 0,
        format_func=lambda i: next(b.title for b in baselines if b.id == i))
    existing = st.session_state.get("candidate_id")

    with st.form("inputs"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Your name (optional)")
        target = c2.text_input("Target role (optional)")
        resume = st.file_uploader("Resume (PDF or DOCX, required)", type=["pdf", "docx"])
        linkedin = st.file_uploader("LinkedIn profile PDF (optional)", type=["pdf"],
                                    help="On LinkedIn, open your profile → More → Save to PDF.")
        c3, c4 = st.columns(2)
        gh = c3.text_input("GitHub username or URL (optional)")
        portfolio_url = c4.text_input("Portfolio URL (optional)")
        portfolio_text = st.text_area("Portfolio content (optional; paste it if the page is built with JavaScript)")
        st.markdown("**Certifications**")
        certs = st.data_editor([{"name": "", "issuer": "", "date": "", "credential_id": "", "verify_url": ""}],
                               num_rows="dynamic", hide_index=True, use_container_width=True, key="cert_rows")
        st.markdown("**References**")
        refs = st.data_editor([{"name": "", "relationship": "manager", "contact": "", "statement": ""}],
                              num_rows="dynamic", hide_index=True, use_container_width=True, key="ref_rows",
                              column_config={"relationship": st.column_config.SelectboxColumn(
                                  options=["manager", "supervisor", "peer", "other"], required=True)})
        rerun_ok = True
        if existing:
            st.warning("Extracting again replaces this candidate's saved profile, including your review edits "
                       "and added-back items.")
            rerun_ok = st.checkbox("I understand, replace the saved profile")
        go = st.form_submit_button("Extract", type="primary")

    if not go:
        return
    if resume is None:
        st.error("Upload a resume to get your score.")
        return
    if not rerun_ok:
        st.error("Tick the box to confirm replacing the saved profile.")
        return
    inputs = OnboardingInputs(
        resume=Upload(resume.name, resume.getvalue()),
        linkedin_pdf=Upload(linkedin.name, linkedin.getvalue()) if linkedin else None,
        github=gh or None, portfolio_url=portfolio_url or None, portfolio_text=portfolio_text or None,
        certifications=[CertificationForm(r["name"], r.get("issuer") or None, r.get("date") or None,
                                          r.get("credential_id") or None, r.get("verify_url") or None)
                        for r in certs if (r.get("name") or "").strip()],
        references=[ReferenceForm(r["name"], r.get("relationship"), r.get("contact") or None,
                                  r.get("statement") or None) for r in refs if (r.get("name") or "").strip()],
        name=name or None, target_role=target or None, candidate_id=existing,
    )
    view = GraphView(st.empty())
    try:
        with st.status("Extracting your profile…", expanded=True) as status:
            res = services.pipeline().start_onboarding(inputs, st.session_state.baseline_id,
                                                       on_progress=st.write, on_event=view.on_event)
            status.update(label="Extraction finished", state="complete")
    except Exception as exc:
        services.show_error("Extraction", exc)
        return
    finally:
        view.draw(force=True)
        st.session_state.last_graph = view
    if res.get("blocked") and not res.get("awaiting_review"):
        st.error(res["blocked"])
        return
    st.session_state.candidate_id = res["candidate_id"]
    st.session_state.thread_id = res["thread_id"]
    st.session_state.onboarding_warnings = (res.get("awaiting_review") or {}).get("warnings", [])
    st.rerun()


# --- Step 2: review ----------------------------------------------------------------------------------

def role_editor(conn, cid, role=None) -> None:
    key = role.id if role else "new_role"
    with st.form(f"role_{key}"):
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("Title", role.title if role else "")
        employer = c2.text_input("Employer", (role.employer or "") if role else "")
        industry = c3.text_input("Industry", (role.industry or "") if role else "")
        c4, c5, c6, c7 = st.columns(4)
        start = c4.date_input("Start", role.start_date if role else None, format="YYYY-MM-DD")
        end = c5.date_input("End", role.end_date if role else None, format="YYYY-MM-DD")
        current = c6.checkbox("Current role", role.is_current if role else False)
        team = c7.number_input("Team size", 0, 10_000, int(role.team_size or 0) if role else 0)
        bullets = st.text_area("Bullets (one per line)", _bullets_text(role) if role else "", height=140)
        b1, b2 = st.columns(2)
        save = b1.form_submit_button("Save role" if role else "Add role")
        delete = b2.form_submit_button("Delete role") if role else False
    if save:
        try:
            store.save_role(conn, cid, role_id=role.id if role else None, title=title, employer=employer,
                            industry=industry, start_date=start, end_date=end, is_current=current,
                            team_size=team or None, bullets=_lines(bullets))
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if delete:
        store.delete_item(conn, "roles", role.id)
        st.rerun()


def project_editor(conn, cid, project=None) -> None:
    key = project.id if project else "new_project"
    with st.form(f"project_{key}"):
        c1, c2 = st.columns([1, 2])
        kind_options = PROJECT_KINDS
        kind = c1.selectbox("Kind", kind_options,
                            index=kind_options.index(project.kind) if project and project.kind in kind_options else 0)
        name = c2.text_input("Name", project.name if project else "")
        description = st.text_input("Description", (project.description or "") if project else "")
        c3, c4, c5 = st.columns(3)
        url = c3.text_input("URL", (project.url or "") if project else "")
        start = c4.date_input("Start", project.start_date if project else None, format="YYYY-MM-DD")
        end = c5.date_input("End", project.end_date if project else None, format="YYYY-MM-DD")
        bullets = st.text_area("Bullets (one per line)", _bullets_text(project) if project else "", height=100)
        b1, b2 = st.columns(2)
        save = b1.form_submit_button("Save project" if project else "Add project")
        delete = b2.form_submit_button("Delete project") if project else False
    if save:
        try:
            store.save_project(conn, cid, project_id=project.id if project else None, kind=kind, name=name,
                               description=description, url=url, start_date=start, end_date=end,
                               bullets=_lines(bullets))
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if delete:
        store.delete_item(conn, "projects", project.id)
        st.rerun()


def step_review(conn, cid) -> None:
    profile = store.load_profile(conn, cid)
    for w in st.session_state.get("onboarding_warnings", []):
        st.info(w)
    blocker = scoring_blocker(profile)
    if blocker:
        st.error(blocker)

    st.subheader("Roles")
    for r in profile.roles:
        flag = " ⚠️" if r.needs_review else ""
        edited = " · edited" if r.source == "user_edit" else ""
        with st.expander(f"{r.title}" + (f" — {r.employer}" if r.employer else "") + f"{flag}{edited}",
                         expanded=r.needs_review):
            _review_flags(r.review_reasons)
            role_editor(conn, cid, r)
            _quote(r.quote)
    with st.expander("➕ Add a role"):
        role_editor(conn, cid)

    st.subheader("Projects")
    for p in profile.projects:
        flag = " ⚠️" if p.needs_review else ""
        gh = " · GitHub" if p.is_github else ""
        fork = " · fork without own commits (not counted)" if p.is_fork and not p.own_commits else ""
        with st.expander(f"{p.name} ({p.kind}){gh}{fork}{flag}", expanded=p.needs_review):
            _review_flags(p.review_reasons)
            project_editor(conn, cid, p)
            _quote(p.quote)
    with st.expander("➕ Add a project"):
        project_editor(conn, cid)

    st.subheader("Skills & tools")
    claims = [c for c in profile.claims if c.kind in ("skill", "tool", "award")]
    for c in claims:
        c1, c2 = st.columns([5, 1])
        where = "in a bullet" if c.bullet_id else "listed"
        c1.write(f"**{c.name_raw}** · {c.kind} · {where}" + (" · added by you" if c.source == "user_edit" else ""))
        if c2.button("Remove", key=f"rm_claim_{c.id}"):
            store.delete_item(conn, "claims", c.id)
            st.rerun()
    with st.form("add_claim"):
        c1, c2, c3 = st.columns([1, 3, 1])
        kind = c1.selectbox("Kind", ["skill", "tool", "award"])
        name = c2.text_input("Name")
        if c3.form_submit_button("Add") and name.strip():
            store.add_claim(conn, cid, kind, name)
            st.rerun()

    st.subheader("Outcomes")
    st.caption("Bullets that state a number (read-only; edit them in the role or project above).")
    outcomes = [b.text for b in profile.all_bullets() if re.search(r"\d", b.text)]
    st.markdown("\n".join(f"- {t}" for t in outcomes) or "_None yet._")

    st.subheader("Certifications")
    for c in profile.certifications:
        c1, c2 = st.columns([5, 1])
        verified = "verified (credential ID or link)" if c.verified else "not verifiable (add a credential ID or link)"
        c1.write(f"**{c.name}**" + (f" · {c.issuer}" if c.issuer else "") + f" · {verified}")
        if c2.button("Remove", key=f"rm_cert_{c.id}"):
            store.delete_item(conn, "certifications", c.id)
            st.rerun()
    with st.form("add_cert"):
        c1, c2, c3, c4 = st.columns(4)
        name = c1.text_input("Certification")
        issuer = c2.text_input("Issuer")
        cred = c3.text_input("Credential ID")
        url = c4.text_input("Verify URL")
        if st.form_submit_button("Add certification") and name.strip():
            store.save_certification(conn, cid, name=name, issuer=issuer, issued_date=None, credential_id=cred,
                                     verify_url=url)
            st.rerun()

    dropped = store.list_dropped(conn, cid)
    st.subheader(f"Dropped items ({len(dropped)})")
    st.caption("These were extracted but their quote was not found in your document, so they were left out. "
               "Add back anything that is correct.")
    evidence = {e["id"]: e["type"] for e in store.list_evidence(conn, cid)}
    for d in dropped:
        c1, c2 = st.columns([5, 1])
        c1.write(f"**{d['item_type']}** · {d['text']} · from {evidence.get(d['evidence_id'], 'source')}")
        if c2.button("Add back", key=f"addback_{d['id']}"):
            try:
                store.restore_dropped(conn, d["id"])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.divider()
    show_last_run()
    if st.button("Confirm & score", type="primary", disabled=bool(blocker)):
        prev = st.session_state.get("last_graph")
        view = GraphView(st.empty())
        if prev is not None:   # keep the onboarding steps green from the extraction run
            view.state.update({k: v for k, v in prev.state.items() if k in ("ingest", "extract", "validate")})
            view.state["review"] = "done"
        try:
            with st.status("Scoring…", expanded=True) as status:
                pipe = services.pipeline()
                thread = st.session_state.get("thread_id")
                if thread and pipe.graph.get_state(pipe._cfg(thread)).interrupts:
                    res = pipe.confirm_review(thread, on_progress=st.write, on_event=view.on_event)
                else:  # e.g. after an app restart: the profile is saved, score it directly
                    res = pipe.score(cid, st.session_state.baseline_id, on_progress=st.write,
                                     on_event=view.on_event)
                status.update(label="Scoring finished", state="complete")
        except Exception as exc:
            services.show_error("Scoring", exc)
            return
        finally:
            view.finish()
            st.session_state.last_graph = view
        if res.get("run_id"):
            st.session_state.run_id = res["run_id"]
            st.session_state.pop("thread_id", None)
            st.switch_page("pages/3_Score.py")
        else:
            st.error(res.get("blocked") or "Scoring did not start.")


with services.connection() as conn:
    cid = st.session_state.get("candidate_id")
    has_profile = bool(cid) and bool(store.list_evidence(conn, cid))
    tab_review, tab_inputs = st.tabs(["2 · Review", "1 · Inputs"]) if has_profile else (None, None)
    if has_profile:
        with tab_review:
            step_review(conn, cid)
        with tab_inputs:
            step_inputs(conn)
    else:
        step_inputs(conn)
