# 15 — Market baseline (sliding window)

Build the pipeline that turns real job postings into a baseline for each of the three roles (AI Engineer, Data Scientist, Product Manager), versioned over time, with a toggle between the current **Manual** baselines and the new **Market** baselines.

**Build everything now; run it at scale later.** Until a paid model is available, it runs end to end on 2–3 job descriptions, plus a no-model test mode on hand-labelled fixtures. Scoring formulas don't change; only the baseline content they read changes.

## 1. Pipeline overview
```
Sources → Fetch (code) → Store & dedupe (code) → Classify role and level (code, model fallback)
→ Extract once per posting (model, cached) → Normalize names (code + reviewed synonym map)
→ Aggregate over the window (code) → Draft version → Human review → Publish
```
Only "Extract" (and the fallback in classification) uses a model, and only once per new posting. Everything else is code.

## 2. Sources
- `config/market_sources.yaml`, per role: 15 companies in three tiers (5 large tech, 5 mid-size, 5 small startups), each with `name`, `tier`, `source` and `source_id`.
  - `source` is one of `greenhouse`, `lever`, `ashby` (public job-board APIs), or `manual`.
- Fill the company list with placeholders and a clear TODO. **I will choose the real companies.**
- Fetchers call only official public job-board APIs. Check each API's current docs and terms before coding it. No scraping of company career sites, LinkedIn or Indeed.
- **Manual source:** a "Add postings" screen (paste text or upload PDF/DOCX, with company, tier, URL and posted date) for companies without a public API, including most large tech companies. This is also how the first 2–3 test postings get in.
- Title filter per role (keywords + exclusions, in config) so only matching roles are kept, e.g. AI Engineer matches "AI Engineer", "ML Engineer, Applied AI", "LLM Engineer" and excludes "Sales Engineer".

## 3. Store, dedupe, window
- Table `market_postings`: id, role, company, tier, source, source_url, title, location, level, `text_hash`, normalized text, raw file, `first_seen`, `last_seen`, status.
- Dedupe: same company + near-identical text (normalized text hash, then a similarity check) = one posting (reposts and multi-city copies).
- **Window:** a posting counts if `last_seen` is within `WINDOW_DAYS` (default 90). Fetching runs weekly and only updates `last_seen` for postings still open (no model calls).
- **Recompute cadence:** `RECOMPUTE_DAYS` (default 30), or on demand from the UI.

## 4. Level
- Code first: stated years ("0–2", "new grad", "1+"), title words (Associate, Junior, I/II, Senior, Staff, Lead, Principal).
- Model only when code can't decide (same extraction call; no extra call).
- Each posting gets `entry` / `mid` / `senior` / `lead`. The entry baseline uses only entry postings; others are kept for future level baselines.

## 5. Extract (model role `MARKET_EXTRACT_MODEL`, configurable)
- Reuse the spec 14 JD extraction schema and rules (quotes, quote check, list splitting, nothing from benefits or EEO text).
- Cached by `text_hash`: a posting is never extracted twice.
- **Test mode, no model:** `tests/fixtures/market/` holds 3 posting texts per role with hand-labelled extraction JSON. The whole pipeline after extraction runs on these.

## 6. Normalize names
- Canonical skill and tool names come from a reviewed synonym map (`data/market_synonyms.json`): e.g. "sklearn" → "scikit-learn", "Large Language Models" → "LLM application development".
- New names not in the map are queued as **suggestions** (code: case, punctuation and plural matching; model only to propose a merge for leftovers, in batches). A person approves or rejects each merge in the UI. Unapproved names stay separate.
- The existing manual baselines' criterion names and aliases seed the map, so Market and Manual criteria line up.

## 7. Aggregate (code only)
For each role and level, over postings in the window:

1. **Per company:** for each criterion, `share_c` = postings listing it ÷ postings of that company; record whether listed as required or preferred.
2. **Per tier:** average `share_c` across the tier's companies that have postings.
3. **Overall:** average of the three tiers (each tier equal; configurable tier weights).
4. **Classification** (config, defaults are proposals):
   - required if listed as required by ≥ `REQ_SHARE` (0.5) overall;
   - preferred if listed at all by ≥ `PREF_SHARE` (0.2);
   - otherwise dropped.
   - **Hysteresis:** a criterion already in a category moves only if it crosses the threshold by `MARGIN` (0.05), so items near a boundary don't flip monthly.
5. `required_years` = median of stated minimum years (entry postings).
6. `expected_reach` = most common reach level from responsibility lines, using the existing `reach` judge labels or the extraction's drafted reach.
7. `scope_expectation`, `complexity_criteria`, `description`: one summarizing call per role per version over the responsibility lines, each sentence with supporting quotes. Fallback: keep the previous version's text.
8. **Minimum data:** publishing needs ≥ `MIN_COMPANIES` (8) and ≥ `MIN_POSTINGS` (20). Below that, the version can be computed and viewed but is marked **"Insufficient data"** and cannot be the active baseline unless "Allow test versions" is on (for the 2–3 posting test).

## 8. Versions and review
- Each recompute creates a **draft version** with its window dates, posting and company counts, and content hash.
- **Market insights** page per role:
  - Table: criterion, overall share, share by tier, required/preferred, change vs the active version (new, dropped, moved, share Δ), number of postings and companies, example quotes.
  - Years, reach, scope and complexity text.
  - A diff against the active version.
  - Buttons: **Publish** / **Discard**. Nothing goes live without a person publishing it.
- Publishing a version is only possible when it differs materially from the active one (a criterion changes category, or years or reach change); otherwise "No material change; active version kept."

## 9. The toggle
- Settings → **Baseline source** per role: **Manual** (today's seed baselines, default) or **Market** (latest published version).
- Candidate scoring for a role uses the selected source. Each run records the baseline source and version.
- Switching source changes the baseline hash, so job-dependent judgments for that role are redone on the next scoring. Show "Switching will re-score N candidates (about M model calls)" before switching.
- Jobs created by recruiters keep their own JD-based baseline; the Market baseline is offered as a template in "Copy an existing job".
- Percentile pools only compare runs with the same baseline version.

## 10. Cost and run plan (report in the dry run)
- Model calls = new postings × 1 extraction + 1 summary per role per version + a few merge batches.
- Fetching and aggregation are free.
- Provide `scripts/market_dry_run.py` that counts postings per source, new postings, model calls and days at the configured limits, without calling any model.

## 11. Tests
- Fetchers against recorded API responses (no network).
- Dedupe, window inclusion by `last_seen`, level rules.
- Aggregation maths: per-company shares, tier averaging, thresholds, hysteresis, minimum-data rule, using the fixtures.
- Version diff; publish blocked without material change; toggle re-score warning.
- An end-to-end test from fixture postings to a published test version, no model.

## 12. Report
- The Market insights page from fixtures and from 2–3 real postings I add (including `AI_Engineer_JD_Brightwater.pdf`).
- Side by side: Manual vs Market baseline for AI Engineer.
- The dry-run counts.
