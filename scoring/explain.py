"""Plain-language formula per subcategory, for the UI and API (specs/04)."""

FORMULAS = {
    "relevance": "Σ(months × relevance) / Σ months over roles",
    "depth": "relevant years ÷ max(1, required years), capped at 1; entry level: max(work, Σ project relevance ÷ 3)",
    "scope": "most recent relevant role vs the baseline's scope: meets 1 · partly 0.5 · below 0",
    "progression": "most recent role vs earlier ones: more impactful 1 · same 0.5 · less 0 (N/A with one role)",
    "recency": "max(relevance × 0.9^years since the activity ended)",
    "role_skills": "(2 × required + preferred) ÷ 3; per skill: bullet 1 · keyword 0.5 · missing 0",
    "applied_knowledge": "(2 × required + preferred) ÷ 3; per skill: task+result 1 · task 0.5 · mention 0",
    "tools": "(2 × required + preferred) ÷ 3; per tool: used/certified 1 · listed 0.5 · missing 0",
    "complexity": "max over relevant roles/projects: meets 1 · partly 0.5 · no 0",
    "work_samples": "1 if any repo, portfolio project, presentation or publication is relevant, else 0",
    "outcomes": "max over bullets of (metric + direction: both 1 · one 0.5) × relevance",
    "impact": "max over bullets of (business 1 · operational 0.5) × relevance",
    "ownership": "max over bullets of (led 1 · contributed 0.5) × relevance",
    "scope_of_impact": "expected reach − best reach: ≤0 → 1 · 1 → 0.5 · ≥2 → 0",
    "recognition": "(relevant award + relevant publication) ÷ 2",
    "proof_density": "average best support per claim: artifact 1 · bullet 0.5 · claimed only 0",
    "validation_strength": "manager/supervisor reference confirming relevant work 1 · verified relevant certificate 0.5",
    "consistency": "1 if LinkedIn, GitHub or portfolio aligns with the resume, else 0",
    "evidence_quality": "average over roles/projects of (yes checks ÷ 5)",
}
