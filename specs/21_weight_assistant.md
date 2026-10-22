# Spec 21 — Recruiter weight assistant (describe what you're looking for → suggested weights)

Status: approved by Feroz (2026-10-22). Scope: **weights only** (dimension and subcategory weights). No must-haves, no scope/complexity settings, no baseline changes. Spec 20 stays reserved for the quality work (gold set, quote validation, backed vs claimed, verified references).

## 0. Principle
**The model suggests, the recruiter decides, code does the math.** Weights remain a company preference layer: they don't change any label or formula, only how saved subcategory scores are combined.

## 1. Where it lives
Recruiter → Job settings → Weights section:
- the 3 existing presets (unchanged, one click each);
- a text box "Describe what you're looking for in this role" (max 800 characters, with a short placeholder example);
- a "Suggest weights" button;
- the existing sliders stay, and remain editable after anything is applied.

## 2. The suggestion call
1. **New model role `WEIGHTS_MODEL`**, using the routing list of the plan task (failover as usual). One call per suggestion. If no model is available, show "Suggestions are unavailable right now. Use a preset or the sliders." The presets act as the template fallback.
2. **Shared, model-neutral prompt containing:**
   - a plain description of the EEAT framework;
   - each dimension and subcategory with a one-line definition of what it measures and what raising its weight does to ranking;
   - the current job weights and the team default table;
   - the job's title, level and required years;
   - the recruiter's text, inside tags and treated as data.
   - **No candidate or applicant data is ever sent.**
3. **Output (JSON):**
   - weights for the 4 dimensions;
   - weights for every subcategory per dimension;
   - a reason of 20 words or fewer for every weight it changes;
   - a summary of 40 words or fewer;
   - `ignored`: the phrases it did not use (see §4).

## 3. Validation in code
4. Accept only known dimension and subcategory keys; numbers ≥ 0. Missing keys keep their current values.
5. Bounds (config): dimension weights between `WEIGHT_DIM_MIN` (5) and `WEIGHT_DIM_MAX` (60); subcategory weights 0–100 within their group. Each group normalized to 100.
6. Round to `WEIGHT_STEP` (5), then fix rounding so every group sums to exactly 100.
7. Invalid or out-of-range output → retry once on the next model in the list, then show "Couldn't produce a suggestion. Try rephrasing or use a preset."
8. **Determinism:**
   - temperature 0 (or the provider's most deterministic setting);
   - suggestions cached by (normalized text, current weights, job baseline hash, model), so the same text gives the same suggestion.
   - A "Suggest again" button bypasses the cache.

## 4. Fairness guardrails
9. The prompt tells the model to ignore anything not job-related, list it in `ignored`, and never let it influence weights. That includes:
   - age;
   - gender;
   - race or ethnicity;
   - religion;
   - nationality or citizenship (work authorization is handled elsewhere);
   - disability;
   - pregnancy or family status;
   - "native speaker" or accent;
   - appearance;
   - "culture fit";
   - school prestige;
   - personal social media.
10. **Code double-check:** a reviewed keyword list (`config/weight_text_screen.yaml`) is matched against the recruiter's text. Any hit is added to `ignored` even if the model missed it.
11. **Recruiter sees:** "Some parts of your description can't be used for ranking and were ignored: …" (neutral wording, no lecture). The suggestion still covers the job-related parts.

## 5. Showing the suggestion
12. A label: "Suggested by AI · you decide" (no model or provider name).
13. **Change table:** current → suggested for every weight that changed, each with an arrow and its reason. Unchanged weights are collapsed under "No change (n)". The summary goes on top.
14. **Ranking preview (no model calls):**
    - re-aggregate the saved subcategory scores of the job's current applicants with the suggested weights, exactly as the existing weight change does;
    - show the top 10 before → after, with movement (e.g. "↑4"), plus counts of applicants moving up, down and unchanged;
    - with fewer than 2 scored applicants, show "No applicants to preview yet".
15. **Buttons:** "Apply weights" (primary) and "Discard".
    - Apply updates the job weights, re-aggregates with no model calls, and offers "Undo" (restores the previous weights).
    - Nothing changes until Apply is clicked.

## 6. Logging
16. New table `weight_suggestions` with these fields:
    - job;
    - recruiter (demo user);
    - text;
    - model;
    - validated weights;
    - ignored phrases;
    - applied yes/no;
    - applied_at;
    - previous weights.
17. Job settings shows the last 5 suggestions (date, short text, applied or not), and each can be re-opened.

## 7. Evaluation
18. A sample set of 10 descriptions in `tests/fixtures/weight_texts.yaml` with expected direction of change. Examples:
    - "extensive project experience and certifications" → Expertise/Work samples/Complexity up, Validation strength up;
    - "senior people who've led teams" → Authority/Ownership/Scope of impact up;
    - "someone who will work closely with product and stakeholders" → Scope of impact up;
    - 2 texts containing protected attributes, which must be ignored;
    - 1 vague text ("a great engineer"), which should give small changes and say so.
19. Offline tests use recorded model outputs. A live run on the sample set happens only after Feroz says go, within free limits. Report each suggestion for his review: weights, reasons, ignored phrases, preview on the AI Engineer demo job.

## 8. Tests (offline)
- validation (unknown keys, negatives, bounds);
- normalization and rounding to exactly 100;
- invalid output → retry → message;
- keyword screen;
- `ignored` shown;
- cache hit for the same text;
- the preview equals an actual re-aggregation;
- Apply and Undo;
- logging;
- no applicant data in the model input (assert on the prompt);
- presets and sliders unchanged.

## 9. Rules
- Scoring formulas, labels and `logic_version` v0.1 unchanged. Weights only change aggregation.
- Archetype regression identical.
- Prompts model-neutral; HITL (nothing applied without a click).
- Tests offline; `DEMO_EMAIL_TO` enforced.

## 10. Report
- what was built;
- tests (counts);
- screenshots:
  - text box;
  - suggestion with the change table and ignored notice;
  - ranking preview;
  - applied + undo;
  - history;
- the live sample-set results (after go);
- decisions marked "my interpretation; please confirm";
- what you couldn't do.
