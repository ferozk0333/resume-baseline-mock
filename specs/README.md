# Specs

Code comments cite these as `specs/NN §x`. `04_scoring_rules.md` is the source of truth for scoring: where anything
else disagrees with it, follow 04 and raise the conflict.

| Spec | Topic | Status |
|---|---|---|
| [01](01_requirements.md) | Requirements: scope, user flow, edge cases | Base spec (iteration 1) |
| [02](02_architecture.md) | LangGraph graph and nodes | Base spec; the current layout is in [docs/architecture.md](../docs/architecture.md) |
| [03](03_data_model.md) | Data model | Base spec; the current tables are in [docs/schema.md](../docs/schema.md) |
| [04](04_scoring_rules.md) | Scoring rules, N/A vs missing, weights | **Live: source of truth** |
| [05](05_prompts.md) | Extraction, judge and plan prompts | Base spec |
| [06](06_ui.md) | Streamlit UI | Superseded by 10 (the Streamlit app is removed) |
| [07](07_testing.md) | Tests and acceptance checks | Base spec |
| [09](09_speed.md) | Speed | Done |
| [10](10_api_frontend.md) | FastAPI + web app | Done |
| [11](11_recruiter.md) | Recruiter view | Done |
| [12](12_benchmarks.md) | Benchmarks | Done |
| [13](13_recruiter_workflows.md) | Recruiter workspace and decision workflows | Done |
| [14](14_create_job.md) | Create a job | Done |
| [15](15_market_baseline.md) | Market baselines | Done |
| [16](16_hiring_workflow.md) | Hiring workflow | Done |
| [17](17_candidate_experience.md) | Candidate experience | Done |
| [18](18_speed_failover_safety.md) | Speed, failover, code-first extraction, prompt-injection screen | Done; README and résumé parsing, partitioned and grouped judging, Gemma/Groq judging, repo cap and hedging were parked and later removed (recoverable from tag `v0.4-pre-refactor`) |
| [19](19_review_ux.md) | Review UX and dedupe | Done |
| 20 | Quality work (gold set, quote validation, backed vs claimed, verified references) | Reserved |
| [21](21_weight_assistant.md) | Recruiter weight assistant | Built; the live sample run waits for the owner's go |

Earlier notes: [docs/archive/](../docs/archive/) (iteration 2 plan, spec 18 summary, the cleanup strings list).
