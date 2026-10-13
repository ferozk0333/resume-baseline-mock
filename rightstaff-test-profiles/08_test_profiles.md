# 08 — Test profiles: load, score, report (for Claude Code)

Goal: run the 15 synthetic profiles (+ `DS4_padded`) through the real pipeline against all three baselines, and report the scores. **Do not tune any formula or weight in this phase.**

## 1. Files
- Copy `rightstaff-test-profiles/` into the repo as `tests/fixtures/profiles/`.
- Each `profiles/<role>/<ID>.md` has a header (`Name`, `GitHub-username`, optional `Strip`) and sections: `Resume`, `LinkedIn`, `GitHub` (JSON), `Portfolio`, `Certifications (form)` (JSON), `References (form)` (JSON). "None provided." means absent.
- `DESIGN.md` describes intent and the expected order. Use it for the report only, never as input to the pipeline.

## 2. Render documents (script: `scripts/render_test_profiles.py`)
- Resume → **PDF** for IDs 1, 3, 5; **DOCX** for IDs 2 and 4; `DS4_padded` → PDF. Use a two-column layout for IDs 2 and 4 to test extraction on harder layouts.
- LinkedIn section → PDF laid out like a LinkedIn "Save to PDF" export (name, headline, Experience, Education, Skills, Licenses). Skip when absent.
- Keep the generated files in `tests/fixtures/profiles/rendered/` (gitignored is fine; the script regenerates them).

## 3. Fixture mode for GitHub and portfolio
- New setting `FIXTURE_DIR`. When set, `ingest/github.py` loads the profile's GitHub JSON instead of calling the API, and `ingest/portfolio.py` uses the profile's Portfolio text instead of fetching a URL. No network calls in fixture mode.
- The GitHub JSON already has `is_fork`, `own_commits`, `archived`, `languages`, `readme` and `last_push`. Map them to the existing ingest fields; apply the same counting rules (forks with 0 own commits are not counted).
- Certifications and references go in as form inputs, exactly as the Onboarding page would send them.

## 4. Batch run (script: `scripts/score_test_profiles.py`)
- Onboard each profile with the review step auto-confirmed (no edits), then score it against **all three** baselines: 16 profiles × 3 baselines = 48 scored runs.
- Freeze the baselines for this run and record their hashes in the output.
- Use the real models and the current `.env` settings (`JUDGE=jev` with Claude fallback). Extraction runs once per profile; job-independent judgments are reused across baselines.
- Write `reports/test_profiles_scores.csv`: profile, baseline, final, the 4 dimension scores, all 19 subcategory scores with status, and run ID.
- Write `reports/test_profiles_summary.md` with:
  1. **Per role, the ranking on its own baseline**, with final and dimension scores, next to the expected order from `DESIGN.md`.
  2. **The 16 × 3 grid** of final scores. Each profile should score highest on its own role's baseline.
  3. **Specific checks** (pass/fail with numbers):
     - `DS4_padded` vs `DS4`: difference in final score and in each subcategory. They should be close.
     - Forks with 0 own commits are not counted (AI2, DS3).
     - PM4's peer reference does not get manager credit in Validation strength.
     - Consistency is `scored 0` (not aligned) for DS2 and PM2.
     - ID 5 profiles: every `missing` subcategory has guidance.
     - DS3, AI3, PM3 (no roles): Relevance and Scope status with the `NO_ROLES_RELEVANCE_SCOPE_NA` flag off, and their final scores with it on.
  4. **Extraction health**: quote-check drop count and `needs_review` count per profile, and anything that looks wrongly extracted from the DOCX or two-column files.
  5. Judge usage: Jev vs Claude vs code per question type, and total run time and cost.
- Run it twice more for the three ID 1 profiles with saved judgments cleared, and report how much their scores move (noise check).

## 5. Report back
Paste `test_profiles_summary.md`. Flag anything surprising, but **do not change formulas, weights or prompts** to make the order match the expectation. The comparison with the LLM-judge rankings happens after this.
