"""Admin: what extraction read and dropped, and the prompt-injection screen."""
from __future__ import annotations

import json

from api.context import Api
from core import config
from extraction import store


def register(api: Api) -> None:
    app, conn = api.app, api.conn
    cand_or_404 = api.cand_or_404

    @app.get("/admin/candidates/{candidate_id}/extraction", tags=["admin"])
    def admin_extraction(candidate_id: str):
        """Admin only (specs/18 §4–5): how each document was read (parser log, shadow comparisons) and any hidden text
        removed before reading. Never in candidate or recruiter responses."""
        from ingest.documents import hidden_text
        with conn() as c:
            cand_or_404(c, candidate_id)
            docs = [{"evidence_id": e["id"], "type": e["type"], "status": e["status"],
                     "hidden_text": hidden_text(c, e["file_sha256"]) if e["file_sha256"] else []}
                    for e in store.list_evidence(c, candidate_id)]
            runs = [{**dict(r), **{k: json.loads(r[k]) if r[k] else None for k in
                                   ("reasons_json", "notes_json", "items_json", "comparison_json")}}
                    for r in c.execute("SELECT * FROM parser_runs WHERE candidate_id = ? ORDER BY created_at",
                                       (candidate_id,))]
            from extraction import prompt_guard
            return {"documents": docs, "parser_runs": runs, "flagged_chunks": prompt_guard.admin_flags(c, candidate_id)}

    @app.get("/admin/screening", tags=["admin"])
    def admin_screening():
        """Admin only: candidates with removed hidden text, parser logs or flagged chunks (specs/18 Phase 4), and
        flagged market postings. Never in candidate or recruiter responses."""
        from extraction import prompt_guard
        with conn() as c:
            rows = c.execute(
                "SELECT c.id, c.name, "
                "(SELECT COUNT(*) FROM parser_runs p WHERE p.candidate_id = c.id) AS parser_runs, "
                "(SELECT COUNT(*) FROM prompt_guard_flags f WHERE f.candidate_id = c.id) AS flagged, "
                "(SELECT COUNT(*) FROM evidence_items e JOIN document_texts d ON d.file_sha256 = e.file_sha256 "
                " WHERE e.candidate_id = c.id AND d.hidden_json IS NOT NULL) AS hidden_docs "
                "FROM candidates c ORDER BY c.created_at DESC").fetchall()
            people = [dict(r) for r in rows if r["parser_runs"] or r["flagged"] or r["hidden_docs"]]
            market = [f for f in prompt_guard.admin_flags(c) if f["source"] == "market_posting"]
            return {"candidates": people, "market_flags": market, "enabled": {
                "prompt_guard": config.PROMPT_GUARD, "remove_hidden_text": config.REMOVE_HIDDEN_TEXT},
                "threshold": config.PROMPT_GUARD_THRESHOLD,
                "model_doc": "https://console.groq.com/docs/model/llama-prompt-guard-2-86m"}

