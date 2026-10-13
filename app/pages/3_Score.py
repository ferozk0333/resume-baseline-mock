"""Score: final and dimension scores, subcategories with the judgments behind them. Weights re-aggregate instantly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app import services  # noqa: E402
from app.components.graph_view import GraphView, show_last_run  # noqa: E402
from app.components.weights_sidebar import weights_sidebar  # noqa: E402
from core import config  # noqa: E402
from graph.pipeline import rescore, run_scores, save_run_weights  # noqa: E402
from judges.service import clear_judgments  # noqa: E402
from scoring.aggregate import normalize_weights  # noqa: E402

st.set_page_config(page_title="Score · RightStaff", layout="wide")
st.title("Score")


def run_scoring(cid: str, bid: str, weights: dict, rejudge: bool = False) -> None:
    view = GraphView(st.empty())
    try:
        with st.status("Re-judging…" if rejudge else "Scoring…", expanded=True) as status:
            if rejudge:
                with services.connection() as c:
                    n = clear_judgments(c, cid, bid)
                st.write(f"Cleared {n} saved judgments for this baseline.")
            res = services.pipeline().score(cid, bid, weights, on_progress=st.write, on_event=view.on_event)
            status.update(label="Scoring finished", state="complete")
        st.session_state.run_id = res.get("run_id")
        if res.get("errors"):
            st.warning(f"{len(res['errors'])} judgment(s) failed; affected subcategories are marked error.")
    except Exception as exc:
        services.show_error("Scoring", exc)
    finally:
        view.finish()
        st.session_state.last_graph = view


def judgment_rows(conn, cid: str, trail: list[dict]) -> list[dict]:
    rows = []
    for t in trail:
        if t["method"] == "jev":
            evidence = f"{t.get('subject') or ''}  [judged text; Jev returns no quote]"
        else:
            evidence = t.get("quote") or t.get("subject") or ""
        rows.append({"question": t["question"], "evidence": evidence, "label": t["label"] or "(failed)",
                     "method": t["method"] or "", "model": t["model_version"] or "",
                     "confidence": t.get("confidence"), "reasoning": t.get("reasoning") or ""})
        for u in services.unused_judgments(conn, cid, t["subject_id"], t["question"]):
            low = u["method"] == "jev" and u["confidence"] is not None and \
                u["confidence"] < config.JEV_CONFIDENCE_THRESHOLD
            note = "Jev (not used: below confidence threshold)" if low else f"{u['method']} (not used)"
            rows.append({"question": "  ↳ " + t["question"], "evidence": note, "label": u["label"],
                         "method": u["method"], "model": u["model_version"], "confidence": u["confidence"],
                         "reasoning": u.get("reasoning") or ""})
    return rows


with services.connection() as conn:
    cid, bid = services.pick_candidate_and_baseline(conn, key="score")
    if not cid:
        st.info("No candidates or baselines yet. Start with Onboarding.")
        st.stop()
    run = services.latest_run(conn, cid, bid)
    scores = run_scores(conn, run["id"]) if run else {}
    na = {s for s, v in scores.items() if v["status"] == "na"}
    weights = weights_sidebar(na, run["id"] if run else None, lambda rid, w: save_run_weights(conn, rid, w))

    c1, c2 = st.columns(2)
    if c1.button("Score now" if not run else "Score again (reuses saved judgments)", type="primary"):
        run_scoring(cid, bid, weights)
        st.rerun()
    if run and c2.button("Re-judge (clear saved judgments)"):
        run_scoring(cid, bid, weights, rejudge=True)
        st.rerun()
    show_last_run()
    if not run:
        st.info("No finished run for this candidate and baseline yet.")
        st.stop()

    agg = rescore(conn, run["id"], weights)   # code only: no model call
    st.caption(f"Run {run['started_at'][:16].replace('T', ' ')} · logic {run['logic_version']} · "
               f"judge {(run['models'] or {}).get('judge')}")
    cols = st.columns(5)
    cols[0].metric("Final score", "—" if agg.final is None else f"{agg.final:.1f}")
    for col, d in zip(cols[1:], config.DIMENSIONS):
        v = agg.dimensions[d]
        col.metric(d.title(), "N/A" if v is None else f"{100 * v:.0f}")

    norm = normalize_weights(weights)
    for d in config.DIMENSIONS:
        st.subheader(d.title())
        st.dataframe([{
            "subcategory": config.SUBCATEGORY_LABELS[s],
            "status": services.STATUS_BADGES.get(scores.get(s, {}).get("status"), "—"),
            "score": None if scores.get(s, {}).get("score") is None else round(scores[s]["score"], 3),
            "weight in dimension": f"{100 * norm['subcategories'][d][s]:.1f}%",
            "share of final": f"{100 * agg.effective_weights.get(s, 0):.1f}%",
        } for s in config.SUBCATEGORIES[d]], hide_index=True, use_container_width=True)
        for s in config.SUBCATEGORIES[d]:
            sc = scores.get(s)
            if not sc:
                continue
            with st.expander(f"{config.SUBCATEGORY_LABELS[s]} · {services.STATUS_BADGES[sc['status']]}"):
                st.markdown(f"**Formula:** {services.FORMULAS[s]}")
                if sc["status"] == "missing" and sc.get("guidance"):
                    st.info(sc["guidance"])
                if sc["status"] in ("na", "error"):
                    st.caption(f"Excluded from the average: {sc['details'].get('reason', sc['status'])}")
                if s == "complexity":
                    st.caption("Graded by a reasoning model; can vary slightly between fresh runs.")
                st.markdown("**Breakdown**")
                st.code(services.pretty(services.details_without_trail(sc["details"])), language="json")
                trail = sc["details"].get("judgments") or []
                if trail:
                    st.markdown("**Judgments**")
                    st.dataframe(judgment_rows(conn, cid, trail), hide_index=True, use_container_width=True)
