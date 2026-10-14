"""Gaps: ranked gaps (code) and the improvement plan as action cards. Weights re-price instantly."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app import services  # noqa: E402
from app.components.weights_sidebar import weights_sidebar  # noqa: E402
from core import config  # noqa: E402
from graph.pipeline import current_plan, regenerate_plan, run_gaps, run_scores, save_run_weights  # noqa: E402
from scoring.plan import TYPE_LABELS  # noqa: E402

st.set_page_config(page_title="Gaps · RightStaff", layout="wide")
st.title("Gaps and improvement plan")

with services.connection() as conn:
    cid, bid = services.pick_candidate_and_baseline(conn, key="gaps")
    run = services.latest_run(conn, cid, bid) if cid else None
    if not run:
        st.info("No finished run for this candidate and baseline yet. Score it on the Score page.")
        st.stop()
    na = {s for s, v in run_scores(conn, run["id"]).items() if v["status"] == "na"}
    weights = weights_sidebar(na, run["id"], lambda rid, w: save_run_weights(conn, rid, w))

    st.subheader("Ranked gaps")
    gaps = run_gaps(conn, run["id"], weights)                    # code only
    st.dataframe([{"subcategory": g.label, "status": services.STATUS_BADGES[g.status],
                   "score": round(g.score, 2), "potential": g.gain_text} for g in gaps],
                 hide_index=True, use_container_width=True)

    st.subheader("Improvement plan")
    plan = current_plan(conn, run["id"], weights)              # stored plan re-priced: no model call
    if plan is None and run.get("plan_status") == "generating":   # written after the score (specs/09 §2.6)
        st.info("Writing your improvement plan… it appears here in a few seconds. The ranked gaps above are final.")
        time.sleep(2)
        st.rerun()
    label = "Regenerate plan" if plan else "Generate plan"
    if st.button(label, type="primary"):
        try:
            with st.spinner("Asking the model for a plan…"):
                plan = regenerate_plan(conn, run["id"], services.pipeline().deps.planner, weights)
        except Exception as exc:
            services.show_error("Generating the plan", exc)
        if plan and plan.error:
            st.error(f"The plan could not be generated: {plan.error}. The ranked gaps above are still valid.")

    if plan is None or not plan.actions:
        st.caption("No plan yet." if plan is None else "The plan has no valid actions.")
    else:
        for i, p in enumerate(plan.actions, 1):
            a = p.action
            with st.container(border=True):
                st.markdown(f"### {i}. {a.title}")
                st.caption(f"{TYPE_LABELS[a.type]} · **{p.points_text}**")
                st.write(a.what_to_do)
                if a.must_show:
                    st.markdown("**Must show**\n" + "\n".join(f"- {m}" for m in a.must_show))
                st.markdown("**Gaps addressed:** " + ", ".join(config.SUBCATEGORY_LABELS.get(k, k)
                                                               for k in a.addresses))
                if a.covers_criteria:
                    st.markdown("**Covers:** " + ", ".join(a.covers_criteria))
                if a.builds_on:
                    st.markdown(f"**Builds on:** {a.builds_on}")
        if plan.quick_wins:
            st.markdown("#### Quick wins\n" + "\n".join(f"- {q}" for q in plan.quick_wins))
        if plan.not_actionable:
            st.markdown("#### Not closable by a near-term action\n" + "\n".join(f"- {n}" for n in plan.not_actionable))
        st.caption("Points are the most each action could add if its gaps were fully closed, not a promised score.")
        with st.expander("Validation notes"):
            if not plan.dropped and not plan.first_attempt_errors:
                st.caption("Every action passed validation on the first try.")
            for d in plan.first_attempt_errors:
                st.write(f"Fixed by the retry · {d['title']} ({d['type']}): {'; '.join(d['errors'])}")
            for d in plan.dropped:
                st.write(f"Dropped · {d['title']} ({d['type']}): {'; '.join(d['errors'])}")
