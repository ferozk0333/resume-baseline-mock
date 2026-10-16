"""Weights (experiment) sidebar: whole-number sliders, normalized %, reset, save to run. No model calls."""
from __future__ import annotations

import streamlit as st

from core import config
from scoring.aggregate import normalize_weights


def _key(*parts: str) -> str:
    return "w__" + "__".join(parts)


def _whole_numbers(group: dict[str, float]) -> dict[str, int]:
    """Integer slider values with the same ratios as `group` (only the ratios matter after normalization).

    Trust's redistributed defaults (31.58, 31.58, 26.32, 10.53) become 30, 30, 25, 10.
    """
    for pct in range(100, 0, -1):
        scaled = {k: v * pct / 100 for k, v in group.items()}
        if all(abs(x - round(x)) < 1e-6 for x in scaled.values()):
            return {k: int(round(x)) for k, x in scaled.items()}
    return {k: int(round(v)) for k, v in group.items()}


def _init(defaults: dict, force: bool = False) -> None:
    dims = _whole_numbers(defaults["dimensions"])
    groups = {d: _whole_numbers(defaults["subcategories"][d]) for d in config.DIMENSIONS}
    # A session that still holds decimal values from the old step-0.01 sliders starts from the defaults again.
    stale = any(isinstance(v, float) for k, v in st.session_state.items() if str(k).startswith("w__"))
    for d in config.DIMENSIONS:
        if force or stale or _key(d) not in st.session_state:
            st.session_state[_key(d)] = dims[d]
        for s, w in groups[d].items():
            if force or stale or _key(d, s) not in st.session_state:
                st.session_state[_key(d, s)] = w


def current_weights() -> dict:
    defaults = config.default_weights()
    _init(defaults)
    return {
        "dimensions": {d: st.session_state[_key(d)] for d in config.DIMENSIONS},
        "subcategories": {d: {s: st.session_state[_key(d, s)] for s in config.SUBCATEGORIES[d]}
                          for d in config.DIMENSIONS},
    }


def weights_sidebar(na_subcategories: set[str] = frozenset(), run_id: str | None = None,
                    on_save=None) -> dict:
    """Render the sidebar and return the current (raw) weights."""
    _init(config.default_weights())
    weights = current_weights()
    norm = normalize_weights(weights)
    with st.sidebar.expander("Weights (experiment)", expanded=False):
        st.caption("Changing a weight re-aggregates saved scores instantly. No model is called.")
        st.markdown("**Dimensions**")
        for d in config.DIMENSIONS:
            st.slider(d.title(), 0, 100, step=1, key=_key(d))
            st.caption(f"{100 * norm['dimensions'][d]:.1f}% of the final score")
        for d in config.DIMENSIONS:
            with st.expander(f"{d.title()} subcategories"):
                for s in config.SUBCATEGORIES[d]:
                    st.slider(config.SUBCATEGORY_LABELS[s], 0, 100, step=1, key=_key(d, s))
                    note = " · N/A for this run, weight redistributed" if s in na_subcategories else ""
                    st.caption(f"{100 * norm['subcategories'][d][s]:.1f}% of {d}{note}")
        c1, c2 = st.columns(2)
        # Reset runs as a callback: Streamlit only lets widget values change before the widgets are drawn.
        c1.button("Reset to table", key="w_reset", on_click=_init, args=(config.default_weights(),),
                  kwargs={"force": True})
        if run_id and on_save and c2.button("Save weights to this run", key="w_save"):
            on_save(run_id, current_weights())
            st.success("Saved to this run.")
    return current_weights()
