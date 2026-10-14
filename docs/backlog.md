# Backlog

## From the retired Streamlit UI (`legacy/app/`), not yet in the web app
Recorded when Streamlit was retired in phase 10. Not scheduled; phase 11 does not wait for them.

1. **Live pipeline diagram.** Streamlit showed the LangGraph run as a Graphviz diagram that updated per node
   (running, scored, missing, N/A, error) and a static copy of the last run (`legacy/app/components/graph_view.py`).
   The web app shows progress lines only.
2. **Discarded low-confidence Jev answers.** On the Score page Streamlit listed, per judgment, the answers that were
   saved but not used (Jev below the confidence threshold, then Claude), with confidence and model
   (`services.unused_judgments`).
3. **"Save weights to this run".** Streamlit could store the slider weights on the run. The API has
   `PUT /runs/{id}/weights`; the web app has no button for it.
4. **The plan's dropped actions and first-attempt errors.** Streamlit showed actions the validator dropped and the
   errors fixed by the retry. The API returns `dropped`; the web app does not show them, and the first-attempt
   errors are not returned yet.
