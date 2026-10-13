"""Baselines: list, create, edit, delete, reset seed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app import services  # noqa: E402
from core import db  # noqa: E402
from core.models import REACH_LABELS, Baseline, BaselineCriterion, level_years_warning  # noqa: E402

st.set_page_config(page_title="Baselines · RightStaff", layout="wide")
st.title("Baselines")

with services.connection() as conn:
    baselines = db.list_baselines(conn)
    st.dataframe([{
        "title": b.title, "level": b.level, "required years": b.required_years,
        "required items": sum(c.priority == "required" for c in b.criteria if c.kind != "certification"),
        "preferred items": sum(c.priority == "preferred" for c in b.criteria if c.kind != "certification"),
        "mock": "mock" if b.is_mock else "",
    } for b in baselines], hide_index=True, use_container_width=True)

    if st.button("Reset seed baselines"):
        db.seed_baselines(conn, reset=True)
        st.success("Seed baselines restored from seed/baselines.json.")
        st.rerun()

    st.divider()
    ids = ["__new__"] + [b.id for b in baselines]
    choice = st.selectbox("Create or edit", ids, format_func=lambda i: "New baseline" if i == "__new__"
                          else next(b.title for b in baselines if b.id == i))
    base = None if choice == "__new__" else db.get_baseline(conn, choice)

    # Level and required years live outside the form so the warning updates as you type.
    c1, c2 = st.columns(2)
    level = c1.text_input("Level", base.level if base else "entry", key=f"level_{choice}")
    years = c2.number_input("Required years", 0.0, 40.0, float(base.required_years) if base else 1.0, 0.5,
                            key=f"years_{choice}")
    warning = level_years_warning(level, years)
    if warning:
        st.warning(warning)

    with st.form(f"baseline_form_{choice}"):
        title = st.text_input("Title", base.title if base else "")
        reach = st.selectbox("Expected reach", list(REACH_LABELS), index=(base.expected_reach - 1) if base else 1,
                             format_func=lambda r: f"{r} · {REACH_LABELS[r]}")
        scope = st.text_area("Scope expectation", base.scope_expectation if base else "")
        complexity = st.text_area("Complexity criteria", base.complexity_criteria if base else "")
        description = st.text_area("Description (short JD)", base.description if base else "")
        st.markdown("**Criteria** (aliases comma-separated)")
        rows = [{"id": c.id, "kind": c.kind, "name": c.name, "description": c.description or "",
                 "aliases": ", ".join(c.aliases), "priority": c.priority} for c in (base.criteria if base else [])]
        edited = st.data_editor(
            rows or [{"id": None, "kind": "skill", "name": "", "description": "", "aliases": "",
                      "priority": "required"}],
            num_rows="dynamic", hide_index=True, use_container_width=True, key=f"crit_{choice}",
            column_config={
                "id": None,
                "kind": st.column_config.SelectboxColumn(options=["skill", "tool", "certification"], required=True),
                "priority": st.column_config.SelectboxColumn(options=["required", "preferred"], required=True),
            })
        submitted = st.form_submit_button("Save baseline", type="primary")

    if submitted:
        try:
            criteria = [BaselineCriterion(id=r.get("id") or None, kind=r["kind"], name=r["name"] or "",
                                          description=r.get("description") or None,
                                          aliases=[a for a in (r.get("aliases") or "").split(",")],
                                          priority=r["priority"])
                        for r in edited if (r.get("name") or "").strip()]
            b = Baseline(id=base.id if base else None, title=title, level=level, required_years=years,
                         expected_reach=reach, scope_expectation=scope or None, complexity_criteria=complexity or None,
                         description=description or None, is_mock=base.is_mock if base else False,
                         criteria=criteria)
            if base:
                db.update_baseline(conn, b)
            else:
                st.session_state.baseline_id = db.create_baseline(conn, b)
            st.success("Saved.")
            st.rerun()
        except ValidationError as exc:
            st.error("Not saved: " + "; ".join(e["msg"] for e in exc.errors()))
        except Exception as exc:
            services.show_error("Saving the baseline", exc)

    if base:
        with st.expander("Delete this baseline"):
            st.caption("Runs for a deleted baseline stay in the history, shown as \"(deleted)\".")
            if st.checkbox(f"Yes, delete {base.title}", key=f"confirm_del_{base.id}") and \
                    st.button("Delete", type="primary", key=f"del_{base.id}"):
                db.delete_baseline(conn, base.id)
                st.success("Deleted.")
                st.rerun()
