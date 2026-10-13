"""RightStaff EEAT scoring mock: home."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from app import services  # noqa: E402
from app.components.graph_view import LEGEND, GraphView  # noqa: E402
from core import db  # noqa: E402
from extraction import store  # noqa: E402

st.set_page_config(page_title="RightStaff", layout="wide")
st.title("RightStaff")
st.markdown(
    "Local mock that scores a candidate profile against a **baseline role profile** with the EEAT framework "
    "(Experience, Expertise, Authority, Trust; 19 subcategories). Models only label evidence; every score is "
    "computed in code and can be traced to the judgments and quotes behind it."
)

with st.expander("How the pipeline works (LangGraph)"):
    st.markdown(
        "Documents are read and extracted **once**, you review the result, then relevance is judged and the four "
        "dimensions run **in parallel**: each of the 19 subcategories judges its evidence, computes its score in code "
        "and saves it. Scores are aggregated with the current weights and the gaps feed the improvement plan. "
        "During a run, the Onboarding and Score pages show this diagram live."
    )
    st.graphviz_chart(GraphView().dot(), use_container_width=True)
    st.caption(LEGEND)

with services.connection() as conn:
    candidates = store.list_candidates(conn)
    baselines = db.list_baselines(conn)
    if not baselines:
        st.warning("No baselines. Create one on the Baselines page.")
        st.stop()

    options = ["__new__"] + [c["id"] for c in candidates]
    cur = st.session_state.get("candidate_id")
    cid = st.selectbox("Candidate", options, index=options.index(cur) if cur in options else 0,
                       format_func=lambda i: "New candidate" if i == "__new__"
                       else services.candidate_label(next(c for c in candidates if c["id"] == i)))
    bids = [b.id for b in baselines]
    cur_b = st.session_state.get("baseline_id")
    bid = st.selectbox("Baseline", bids, index=bids.index(cur_b) if cur_b in bids else 0,
                       format_func=lambda i: next(f"{b.title} ({b.level})" for b in baselines if b.id == i))
    st.session_state.baseline_id = bid

    c1, c2 = st.columns(2)
    if c1.button("Start onboarding", type="primary"):
        st.session_state.candidate_id = None if cid == "__new__" else cid
        st.session_state.pop("thread_id", None)
        st.switch_page("pages/2_Onboarding.py")
    if c2.button("View latest score", disabled=cid == "__new__"):
        st.session_state.candidate_id = cid
        st.switch_page("pages/3_Score.py")

    st.subheader("Past runs")
    runs = db.list_runs(conn, None if cid == "__new__" else cid)
    if not runs:
        st.caption("No runs yet.")
    else:
        names = {c["id"]: c.get("name") or "Unnamed" for c in candidates}
        st.dataframe(
            [{"started": r["started_at"][:16].replace("T", " "), "candidate": names.get(r["candidate_id"], "?"),
              "baseline": r["baseline_title"], "status": r["status"], "logic": r["logic_version"],
              "judge": (r["models"] or {}).get("judge")} for r in runs],
            hide_index=True, use_container_width=True)
