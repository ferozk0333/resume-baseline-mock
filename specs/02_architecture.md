# 02 — Architecture

## Principle
Extract **once** (by evidence type), then judge and compute per subcategory in parallel. Models label; code computes.

## Graph (LangGraph)

```
START
  └─ ingest            parse resume / LinkedIn PDF / GitHub / portfolio → text, save evidence_items
  └─ extract           LLM → Pydantic (roles, projects, bullets, claims, certs, refs), one call per evidence item
  └─ validate          quote check, dates, dedupe, skill normalization → save
  └─ [interrupt]       human review in UI; resume graph after user confirms
  └─ prepare_match     load baseline; judge relevance of every role and project (shared input for many subcategories)
  └─ fan-out (parallel)
       ├─ experience   → relevance | depth | scope | progression | recency
       ├─ expertise    → role_skills | applied_knowledge | tools | complexity | work_samples
       ├─ authority    → outcomes | impact | ownership | scope_of_impact | recognition
       └─ trust        → proof_density | validation_strength | consistency | evidence_quality
  └─ aggregate         dimension + final scores using current weights (code)
  └─ gap_analysis      rank gaps in code → Sonnet returns a structured improvement plan → code validates, prices, renders (a plan failure never fails the run)
END
```

- Each dimension node is a **subgraph**; its subcategory nodes run in parallel.
- Each subcategory node is a small subgraph: `judge → compute → save`. In code each subcategory defines `plan(ctx)` (the judgments it needs, or a rule label when pre-filtering finds no mention) and `compute(ctx, labels)`; `judge` resolves the plan through the cache, `compute` re-plans and reads **saved judgments only** (it can never call a model), `save` writes the score row. Any exception in a subcategory becomes `status = error` for that subcategory only.
- Implementation notes: subgraphs return only reducer keys (`subcategory_scores`, `errors`) so parallel branches never collide; each node opens its own SQLite connection (WAL mode). All 19 subcategories run at once; throughput is set by concurrency caps, not the graph: `MAX_PARALLEL_JUDGE_CALLS` (48, judgments in flight), and separate provider pools `JEV_MAX_CONCURRENCY` (32) and `CLAUDE_MAX_CONCURRENCY` (12) so fast Jev calls never queue the slow reasoning-model calls. Measured on a real profile (~990 judge calls): one shared cap of 8 → 55–62 s; the separate pools → 13.5 s (simulated latency). Human review is a LangGraph `interrupt` in node `review` (in-memory checkpointer); scoring-only runs (another baseline, or after weight edits) start at `prepare_match`.
- Every run records `logic_version`, the weights snapshot, the judge/model settings (`models_json`) and the baseline content hash.
  - `judge`: calls the `Judge` interface for the labels it needs (skipped when the subcategory is pure code).
  - `compute`: pure function from `scoring/formulas.py`.
  - `save`: writes judgments and the score row.
- `prepare_match` runs **before** the fan-out because relevance labels are reused by Relevance, Depth, Recency, Outcomes, Impact, Ownership. Do not judge relevance more than once per role/project per baseline.
- Judgments are cached by `(subject_id, question, baseline_id or null, baseline_hash or null, model_version, input_hash)`. `input_hash` = SHA-256 of the question key and the exact context text sent to the judge (no run id, no timestamp), so an edited bullet is judged again and an unchanged one never is. `baseline_hash` is set only for job-dependent judgments (`baseline_id` not null): SHA-256 of the baseline fields judges see (title, level, description, scope_expectation, complexity_criteria, and each criterion's name/description/aliases; criteria order ignored). Editing any of those fields invalidates that baseline's cached job-dependent judgments; job-independent judgments (`baseline_id = NULL`, `baseline_hash = NULL`) are unaffected. Re-running the same profile against an unchanged baseline makes no new model calls.

## Which subcategories need a model

| Subcategory | Model needed | Notes |
|---|---|---|
| Relevance | relevance labels (from prepare_match) | then code |
| Depth | relevance labels | then code (dates) |
| Scope | yes | compares most recent relevant role to baseline |
| Progression | yes | N/A if one role |
| Recency | relevance labels | then code (dates) |
| Role-specific skills | yes | evidence level per baseline skill |
| Applied knowledge | yes | applied level per baseline skill |
| Tools | yes | evidence level per baseline tool |
| Complexity | yes (Sonnet) | per relevant role/project |
| Work samples | yes | relevance of each work sample |
| Outcomes | yes | metric/direction per bullet; × relevance |
| Impact | yes | impact type per bullet; × relevance |
| Ownership | yes | role in work per bullet; × relevance |
| Scope of impact | yes | reach per bullet + expected reach from baseline |
| Recognition | yes | award / publication relevance |
| Proof density | yes | best support per claim |
| Validation strength | yes | reference relevance; cert verification is code |
| Consistency | yes | resume vs other source alignment |
| Evidence quality | yes | 5 yes/no checks per item |

Job-independent labels (e.g. outcome metric/direction, ownership, impact type, evidence-quality checks, proof support, source alignment) are stored with `baseline_id = NULL` and reused across baselines. In Trust, Proof density, Consistency and Evidence quality are job-independent; **Validation strength is job-dependent** (`reference_confirms` carries `baseline_id` + `baseline_hash`; certificate relevance is matched against the baseline's certification criteria).

Skill/tool questions with no alias match are routed to the Claude judge with all bullets and must return a quote found in the input (see `05`, Pre-filtering).

## Judge interface

```python
class Judgment(BaseModel):
    label: str
    probabilities: dict[str, float] | None
    quote: str | None
    reasoning: str | None
    method: Literal["jev", "claude", "rule"]
    model_version: str

class Judge(Protocol):
    def choice(self, question: str, options: list[str], context: dict) -> Judgment: ...
    def yes_no(self, question: str, context: dict) -> Judgment: ...
```

- `JevJudge`: calls Jev (model pinned by `JEV_MODEL`, default `jev-1.13.0`). If Jev's `confidence` < `JEV_CONFIDENCE_THRESHOLD` (yes/no: |2p − 1|), or the call fails, falls back to `ClaudeJudge` and saves both judgments, using the Claude one. The replaced judgment travels on `Judgment.fallback_from` and is saved with `used = 0`; the cache only returns `used = 1` rows.
- `JudgmentService` (judges/service.py) wraps any judge: cache lookup, save every judgment, record rule labels (`method = rule`), and turn a failed call into `None` (the subcategory decides whether that makes it `error`).
- Judge models: `COMPLEXITY_MODEL` for `complexity` (always Claude, also under `JUDGE=jev`), `JUDGE_FALLBACK_MODEL` for every other Claude judgment.
- `ClaudeJudge`: calls Claude with structured output, temperature 0.
- `FakeJudge`: returns scripted labels for tests.
- `JUDGE=claude` skips Jev entirely.

## Graph state (minimum)

```python
class RunState(TypedDict):
    run_id: str
    candidate_id: str
    baseline_id: str
    weights: dict                 # snapshot used for this run
    errors: list[str]
    subcategory_scores: dict      # subcategory -> {score, status, details}
```
Large data (roles, bullets, claims) lives in SQLite; nodes read it by `candidate_id`, not through the state.

## Project layout

```
rightstaff-eeat-mock/
  CLAUDE.md
  specs/ ...
  seed/baselines.json
  app/                      # Streamlit
    Home.py
    pages/1_Baselines.py
    pages/2_Onboarding.py
    pages/3_Score.py
    pages/4_Gaps.py
    components/weights_sidebar.py
  core/
    config.py               # env, default weights, constants
    db.py                   # connection, migrations, CRUD
    models.py               # Pydantic models
  ingest/
    documents.py            # PDF/DOCX → text
    github.py
    portfolio.py
  extraction/
    extract.py
    validate.py
  judges/
    base.py  claude_judge.py  jev_judge.py  fake_judge.py
  scoring/
    formulas.py             # pure functions, one per subcategory
    aggregate.py            # dimension + final, weights, N/A handling
    gaps.py                 # gain ranking
  graph/
    pipeline.py             # LangGraph build
    nodes/ experience.py expertise.py authority.py trust.py prepare.py
  prompts/                  # prompt text files from 05
  tests/
  rightstaff.db             # created at runtime
  .env.example
  requirements.txt
```
