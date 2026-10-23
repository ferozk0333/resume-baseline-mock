# Spec 18 — Speed, model failover, Jev-assisted extraction, prompt-injection screen

Status: approved by the owner (2026-10-16): "do it all". Phased; stop and report after each phase. Items marked **[verify]** must be checked against provider docs before building.

## 0. Why
**Measured (free tier):**
- Baseline scoring took 5 min 40 s for one role (Data Scientist):
  - 63 Gemini Flash-Lite calls, against a ceiling of 15 per minute that was hit 105 times;
  - about 9 s per call, with only 4 in flight;
  - 20 overload retries waiting 30–60 s;
  - the relevance step blocks the expertise step.
- Jev answered 64 calls at about 0.1 s each, so Jev is not the problem.
- Extraction on 16 Oct waited about 5 min and then failed: Gemini 3.8 Flash was past its 20-per-day limit. Each repo README is its own Flash call. The client waits up to 300 s.
- Google's dashboard shows peaks of 40/20 requests per day and 19/15 requests per minute, so our local counters and Google's disagree.

**Goals:**
- baseline scoring under about 60 s after Confirm;
- extraction as fast as free limits allow;
- no daily-cap stalls;
- no quality loss without an explicit decision.

In production we will use the **paid Claude API**. Everything stays provider-neutral: switching is config only, and there are no per-model prompt branches.

## 1. Free-tier facts (as of 2026-10-16; read live limits from response headers where available)
**Gemini** (one project, separate quota per model):

| Model | RPM | TPM | RPD |
|---|---|---|---|
| gemini 3.8 / 3.7 / 3.6 / 3 / 2.5 Flash | 5 | 250K | 20 each |
| gemini 3.5 Flash Lite, 3.1 Flash Lite | 15 | 250K | 500 each |
| gemma 4 31B, gemma 4 26B | 30 | 16K | 14.4K each |

**Groq** (`GROQ_API_KEY` in .env; organization limits):

| Model | RPM | RPD | TPM | TPD | Use |
|---|---|---|---|---|---|
| openai/gpt-oss-120b | 30 | 1K | 8K | 200K | single-item judging |
| openai/gpt-oss-20b | 30 | 1K | 8K | 200K | single-item judging |
| qwen/qwen3.8-27b | 30 | 1K | 8K | 200K | single-item judging |
| meta-llama/llama-prompt-guard-2-86m / -22m | 30 | 14.4K | 15K | 500K | prompt-injection screen only |

Not used: allam-2-7b, gpt-oss-safeguard-20b, speech models.

**Jev** (TypeSafe, `jev-1.13.0`, pinned):
- returns only Choice (up to 255 options), Score (2–10 levels) and Noul (probability of yes); it writes no text;
- context window listed as 32K tokens;
- pricing listed as $0.042 per million input tokens, output free;
- Jev calls need no approval. **[verify all in docs.typesafe.ai]**

**Rules:**
- Never create extra projects or API keys to multiply quota.
- Only the owner's own or synthetic profiles go to free-tier providers.
- Before any live run, link Google's and Groq's free-tier data-use pages in the report.

## 2. Phase 1: routing, failover, fail fast, early start
No change to *how* questions are asked.

1. **Routing lists per task in config**, as ordered `provider/model` lists across providers. Defaults:

   | Task | Order |
   |---|---|
   | extract_document (resume, LinkedIn) | gemini 3.8 flash → 3.7 → 3.6 → 3 → 2.5 flash |
   | extract_web (README, portfolio) | gemini 3.5 flash lite → 3.1 flash lite → gemma 4 31b → groq gpt-oss-120b |
   | judge_single | gemini 3.5 flash lite → 3.1 flash lite → groq gpt-oss-120b → groq qwen3.8-27b → groq gpt-oss-20b → gemma 4 31b → gemma 4 26b |
   | judge_grouped | gemini 3.5 flash lite → 3.1 flash lite → gemma 4 31b |
   | plan | gemini 3.8 flash → 3.7 → 3.6 flash |
   | email / reply / feedback / job extract | as judge_single (job extract: as extract_document) |

   - Adding `claude/...` at the top of any list must work with no code change. The Anthropic path exists; Sonnet 5.5 rejects the temperature parameter.
   - **In Phase 1, models other than the current ones** (3.8 Flash, 3.5 Flash Lite) are used only as overflow, when the current model is exhausted or overloaded. Phase 2 decides routine use.
2. **Limiter per model:**
   - tracks requests per minute and per day, and tokens per minute and per day;
   - estimates tokens before sending and skips a model the call won't fit (e.g. a resume on an 8K/16K TPM model);
   - **the provider's limit or overload response wins over our counters:** mark the model exhausted until the reset that the error or headers state (Groq: `x-ratelimit-*`, `retry-after`).
3. **Short retries:**
   - on 429, 503 or a timeout, never sleep: send the call immediately to the next model with capacity;
   - only if every model in the list is busy, wait min(retry-after, 5 s) with jitter;
   - max 4 attempts per call across the list;
   - when everything is exhausted for the day, fail fast with a clear message. Never queue onto exhausted models.
4. **Timeouts:** Gemini 45 s, Groq 20 s, Jev unchanged (config).
5. **Concurrency** per model up to its RPM (config), not 4 in total.
6. **Hedging:** flag `HEDGE_AFTER_S`, default off. If a single-item call has no answer after N s, send a duplicate to the next model and keep whichever answers first. Report the quota cost.
7. **Early start.** Target roles are chosen before Review (spec 17), so:
   - start relevance and all role-specific judgments for those roles as soon as extraction finishes;
   - edited items are re-judged automatically, because the saved answer is tied to the exact text;
   - Confirm only finishes what's left.
8. **Dev/test guard:** e2e and dev commands never call real providers unless `LIVE=1`; use fixtures/record-replay.
9. **Traceability:** every judgment and extraction keeps recording provider/model. Runs record calls per task per model and the failovers.

**Report:**
- expected calls per task and model for one onboarding (2 roles);
- failover and fail-fast log lines;
- then, **only after the owner says go**, one live run on the owner's resume with 2 roles, with per-step timings.

## 3. Phase 2: fewer calls + routine use of new model families (flags, default OFF)
10. **`GROUPED_ITEM_JUDGING`:**
    - one call per family for complexity and work-sample questions, as skill/tool checks already work;
    - same output schema;
    - a quote required for any label above the lowest;
    - per-item retry on failure.
    - Complexity keeps "reason first".
11. **`REPO_CAP`:**
    - judge and read READMEs for at most `MAX_REPOS` (default 8), ranked by relevance to the role, then latest commit, then stars;
    - other repos are listed but not judged;
    - forks with 0 own commits are still excluded.
12. **`GEMMA_JUDGE` and `GROQ_JUDGE`:** allow these families in routine judging (not only as overflow).
    - Groq: single-item questions only; never extraction, plans or grouped calls.
    - Same shared prompts.
    - Invalid JSON → retry once on the next model, then missing with the reason.
13. **Comparison:**
    - Run the 16 test profiles with each flag on vs off. Use fixtures where possible; make real calls only within free limits, and **state the call count before running**.
    - Report per profile:
      - score change;
      - label agreement per question family;
      - average seconds per call per model;
      - whether the ranking is still 1 > {3,4} > 2 > 5;
      - any failed DESIGN check.
    - the owner decides per flag.

## 4. Phase 3: Code-first extraction (flags, default OFF). **Build this before Phase 2.**

### 4.1 Why
- Reading text from digital PDFs (`pymupdf`) already takes milliseconds. OCR is not needed: image-only PDFs keep the unreadable warning.
- The current ~2 min is the LLM *writing* about 75 structured items with quotes on a slow, capped free tier.
- Our extraction is richer than job-board parsers (claims inside bullets, skill vs tool, parenthetical rules, award trimming, publication vs certification, quotes). So speed must not cost accuracy.

**Design principle:** **code builds the structure, Jev decides what's unclear, the LLM is a fallback.**
- Every field is a verbatim span of the source, so the quote check always passes and nothing can be invented.
- A document uses the new route only when the parser is confident. Otherwise the whole document falls back to today's LLM extraction through the routing list.
- Record the method per item: parser / jev / llm.

### 4.2 Shared building blocks (code)
14. **Layout reader:**
    - Use `pymupdf` text blocks/spans with coordinates, font size and bold.
    - Detect reading order, including two columns: cluster blocks by x-position; a column is read top to bottom before the next.
    - Bullet markers; line merging for wrapped lines and PDF hyphenation.
    - The existing whitespace normalization and hidden-text removal (Phase 4 item 20, moved here) run first.
    - Keep `pymupdf4llm` Markdown as the source text shown in Review.
15. **Section detector:**
    - A dictionary of header variants per section: experience/work history/employment, projects, education, skills/technical skills, certifications/licences, publications, awards/honours, volunteering, summary, other.
    - Use font and position cues (bigger or bold, alone on a line).
16. **Date parser:**
    - Handles "Jan 2020", "01/2020", "2020-01", "2019–2022", "Present/Current".
    - Inclusive months; year-only → Jan–Dec + `needs_review`.
    - Use a maintained library (e.g. `dateparser`) plus our rules.
17. **Item builder:**
    - Within experience/projects, group lines into items: title, employer, location, dates, then bullets.
    - Unclear line roles go to Jev (item 20).
18. **Claims:**
    - (a) The skills section is split with the existing claim rules (flat lists, the parenthetical platform/category/method rules, "CI/CD" and "A/B testing" kept whole).
    - (b) Inside bullets and descriptions, code finds names from our vocabulary (baseline criteria names and aliases + `config/market_synonyms.json`), and Jev confirms each with a Noul: "the candidate states they use or know this".
    - (c) For bullets that matched no vocabulary, **one small LLM call per document** returns only a list of skill/tool names with their quote (judge_single route). Names must appear verbatim in the bullet; otherwise they are dropped.
19. **Semantic rules in code:**
    - award trimming (keep the event, trim to the clause, ≤ ~120 chars);
    - publication vs certification by section and keywords;
    - skill vs tool display classification (vocabulary first, Jev Choice for unknowns);
    - dedupe;
    - date conflict rules (resume wins; LinkedIn fills empty fields; its more precise date wins when the year agrees).
20. **Jev labelling for unclear lines:**
    - Choice: section heading, job title, employer, date range, location, bullet/description, education, certification, publication, award, skill, other.
    - Noul: "belongs to the same item as the previous line".
    - Multi-question requests.
21. **Confidence gate per document:**
    - Fall back to the LLM if any of these hold:
      - a required section can't be found;
      - more than `PARSER_MAX_UNCLEAR` lines (config) are unclear after Jev;
      - an item has a title but no dates, or dates that don't parse;
      - reading order looks scrambled (dates interleaved across columns);
      - any Jev label is below `JEV_CONFIDENCE_THRESHOLD`.
    - Log the reason.

### 4.3 Per source (separate flags)
22. **`PARSE_README`** (first):
    - Repo metadata from the GitHub API as today.
    - The README is split into blocks. Jev keeps the blocks that "describe what was built" or "describe a result or outcome", and drops setup, install, license, badges and other.
    - The kept blocks, verbatim and in order (length capped in config), become the project description.
    - No LLM call.
23. **`PARSE_LINKEDIN`:**
    - LinkedIn "Save to PDF" has a fixed layout (left column: contact, top skills, languages, certifications; main column: name, headline, summary, experience, education).
    - Build a dedicated parser for it on the shared blocks.
    - No LLM call normally.
24. **`PARSE_RESUME`:**
    - Shared blocks + Jev + the one small claims call (item 18c).
    - Full LLM fallback via the confidence gate.
25. **Portfolio:** unchanged (LLM).

### 4.4 Acceptance gates ("works just as well as now")
A flag can be turned on only if all of these pass. Compare against today's LLM extraction:
26. **Reference** (`scripts/compare_extraction.py`, the owner's resume and LinkedIn PDF in `tests/extraction_reference/`):
    - no fewer items found;
    - no extra items that aren't in the source;
    - dates identical (or more precise);
    - proof density within ±0.01.
27. **16 test profiles:**
    - final scores within ±1 point;
    - ranking still 1 > {3,4} > 2 > 5;
    - all DESIGN checks pass.
28. **Layout stress set** (synthetic only):
    - Re-render the 16 test profiles as single-column, two-column and table-based resumes (DOCX and PDF).
    - Each document must either parse correctly or fall back to the LLM. **Never silently wrong.**
    - Report the pass/fallback/wrong counts per layout.
29. **Timing and calls per source:** parser time, Jev calls, LLM calls, and how often the fallback is used.

Report all gates per flag. The owner decides per flag.

## 5. Phase 4: prompt-injection screen (never affects scores)
20. **Hidden text, in code:**
    - When converting PDF/DOCX, detect hidden text: white or near-background colour, tiny font, off-page.
    - Remove it before extraction, as already done for hidden HTML in portfolio pages.
    - Keep what was removed for Admin.
21. **Llama Prompt Guard 2** (groq `llama-prompt-guard-2-86m`, failover `-22m`):
    - **[verify]** its input window (believed to be about 512 tokens) and chunk accordingly.
    - Screen candidate documents, portfolio pages, READMEs and market postings.
    - It runs **in the background, in parallel with extraction, never blocking it**, cached by text hash.
    - Threshold `PROMPT_GUARD_THRESHOLD` in config.
22. **Actions:**
    - **Hidden text that is also flagged:** stays removed. The candidate sees on Review: "We ignored hidden text in your <document>."
    - **Visible text that is flagged:** kept as is; logged for Admin only (new Admin page listing document, chunk and score).
    - **Never:** change a score, show recruiters a flag, or block onboarding or applying.
    - AI Engineer READMEs often contain prompts, so expect false positives. Report the false-positive rate on the 16 test profiles and on the owner's repos.

## 6. Rules (all phases)
- Scoring formulas, weights and `logic_version` v0.1 unchanged. Archetype regression identical. Jev confidence threshold unchanged.
- Prompts are model-neutral. Every model role is configurable, with template/manual fallback.
- Tests are offline (FakeJudge, fixtures, recorded Jev/Groq/Gemini responses). `DEMO_EMAIL_TO` enforced. No paid calls other than Jev.
- No new candidate- or recruiter-facing wording that names models or providers.

## 7. Tests (offline)
- Routing order, overflow-only behaviour in Phase 1, skipping a model when tokens won't fit.
- Provider 429/503/timeout → immediate failover; all busy → short wait; all exhausted → fail fast with a message.
- Exhaustion persists until the stated reset; Google/Groq responses override local counters.
- Concurrency per model.
- Early start uses the selected roles; edited text is re-judged.
- LIVE guard.
- Each Phase 2–4 flag: off = today's behaviour exactly (regression identical); on = expected call counts.
- Jev extraction: verbatim spans only; quote check passes; low-confidence fallback; method recorded per item.
- Hidden-text removal on fixture PDFs.
- Prompt guard never blocks the pipeline and never changes scores; nothing in recruiter responses.

## 8. Report (each phase)
- what was built;
- tests (counts);
- timings and calls per task and model;
- decisions marked "my interpretation; please confirm";
- what you couldn't do.

Stop after each phase.
