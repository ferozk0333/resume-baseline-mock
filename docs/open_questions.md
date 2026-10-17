# Open questions

Product decisions the scoring rules leave open. Nothing here is implemented; each item needs a decision
before code changes.

The numbers come from the 16 test profiles, scored with real judging (Jev + Gemini 3.5 Flash-Lite), each
against its own role's job with default weights (`data/demo.db`; full table in
`timings/test_profiles_report.md`). The "flag on" numbers were recomputed from the saved subcategory scores,
with no model calls.

## From the test-profile run

### 1. Thin profiles score about 2/100
| Profile | Final | Experience | Expertise | Authority | Trust |
|---|---|---|---|---|---|
| AI5 | 2.2 | 0 | 2 | 0 | 8 |
| DS5 | 2.6 | 0 | 3 | 0 | 8 |
| PM5 | 1.7 | 0 | 0 | 0 | 8 |

All three have 7 subcategories marked `missing`, each with guidance. Those 7 carry **30%** of the final
weight: Scope, Complexity, Work samples, Scope of impact, Recognition, Validation strength and Consistency.

Most of the remaining weight is **scored 0**, not missing: 64% for AI5, 55% for DS5 and 68% for PM5. Some
evidence was found but none of it matched the role, for example Relevance 0 or Proof density 0. The only
non-zero subcategories are Evidence quality (0.8) and a sliver of Tools or Role skills.

**Question:** when most of the weight has no evidence, should the candidate see "Not enough evidence to score
yet" plus the guidance, instead of a number?

If yes, we need to decide what counts as "no evidence". Counting only `missing` gives 30%, so a "most of the
weight" rule would never trigger for these profiles. Counting `missing` plus scored 0 gives 85–98%.

### 2. No-role grads get Authority 90–95 from project bullets
| Profile | Final | Authority | Outcomes | Impact | Ownership | Scope of impact | Recognition |
|---|---|---|---|---|---|---|---|
| DS3 | 64.6 | 95 | 1.0 | 1.0 | 1.0 | 1.0 | 0.5 |
| AI3 | 69.7 | 90 | 1.0 | 1.0 | 1.0 | 1.0 | missing |
| PM3 | 68.0 | 90 | 1.0 | 1.0 | 1.0 | 1.0 | missing |
| *DS1, for reference* | 88.2 | 90 | 1.0 | 1.0 | 1.0 | 1.0 | missing |

The grads have no roles, so every best bullet is a project bullet. Examples:
- "Re-analyzed a public 1.2M-user pricing experiment; found a sample-ratio mismatch…" (DS3)
- "Used by 4 student project teams (about 25 developers) during the spring term." (AI3)
- "Ran a test of push reminders vs none: reminders lifted weekly active use from 29% to 35%." (PM3)

Each counts the same as a job result. DS3's Authority (95) is higher than DS1's (90).

**Question:** should project bullets count fully in Authority? Alternatives:
- discount them by a factor;
- count them only for entry-level baselines;
- leave them as they are.

### 3. No-role grads get Experience 38
With no roles, Relevance (weight 35) and Scope (weight 20) are `missing`, so they score 0. Progression is
N/A. Depth (1.0) and Recency (about 0.95) carry the rest.

**Question:** should `NO_ROLES_RELEVANCE_SCOPE_NA` be turned on? With it on, Relevance and Scope become N/A
and are left out of the Experience average.

| Profile | Experience now → flag on | Final now → flag on | Top profile in the job (final) |
|---|---|---|---|
| DS3 | 38 → 99 | 64.6 → **82.7** | DS1 88.2 |
| AI3 | 38 → 99 | 69.7 → **87.8** | AI1 86.6 |
| PM3 | 38 → 99 | 68.0 → **86.0** | PM1 86.2 |

With the flag on:
- AI3 ranks **above** AI1, and PM3 is 0.2 below PM1. That breaks DESIGN.md's expected order, 1 > {3, 4}.
- A grad with strong projects would match or beat a candidate with the same projects plus relevant jobs.

A middle option would be to treat only Scope as N/A and keep Relevance as missing. It was not computed;
ask if wanted.

## Earlier open items

4. **Authority has no time decay.**
   - Outcomes, Impact, Ownership and Scope of impact take the best bullet, however old.
   - Only Recency (Experience) decays (0.9 per year).
   - Should a strong result from years ago count fully?
5. **Repos don't count for Recency.**
   - For entry-level baselines, Recency includes projects, publications and hackathons, but not repos or
     portfolios.
   - A candidate whose recent work is only on GitHub gets no Recency credit for it. Should recent repos with
     own commits count?
6. **Scope of impact relevance filter** (`SCOPE_OF_IMPACT_RELEVANCE_FILTER = True`).
   - Only bullets judged relevant (relevance > 0) count toward reach.
   - Keep it, or let reach from unrelated work count?
7. **Recruiter presets equal the team table.**
   - "Startup", "Enterprise" and "Proof-first" have storage and UI.
   - Each one returns the default weights until the team sets values.
8. **Recruiter thresholds are proposals.**
   - Strength tags: subcategory score ≥ 0.8 (`TAG_MIN_SCORE`), up to 3.
   - "Older evidence" phone-screen question: best relevant evidence older than 3 years (`STALE_YEARS`).
   - Both are kept as config and need confirming.

## From specs/13 (recruiter workflows)

9. **On-paper floor for the all-bullets route** (specs/13 §0.4). **Decided and applied.** A criterion found only
   in the skills list gets two judgments: the alias route (the skills list, as before) and the all-bullets route
   (subject "<criterion id>#all"). The final label is the better of the two, so the all-bullets route only adds
   credit. Re-score (27 Flash-Lite calls, 0 Jev): AI1 Git and DS3 SQL back to listed (AI1 87.1 → 87.6, DS3 65.5 →
   65.9). The FakeJudge archetype snapshot was re-recorded deliberately: **no archetype score changed**; only the
   judgment-label counts (the extra "#all" judgments). The +0.5 changes estimated earlier came from a code-only
   floor that is not what was applied: with two real judgments, the scripted judge gives both routes the same label.
10. **Lenient bullet credit for a keyword-heavy profile.** PM2 gained "bullet" for Prioritization ("align on
    priorities and roadmaps"), Product analytics ("data-driven decision making") and Cross-functional
    collaboration: final 45.0 → 46.8. Accept, or tighten the definition of "bullet" in the judge prompt?
11. **Failed Gemini attempts and the daily limit.** **Decided and applied:** 503/overloaded answers and requests
    with no answer are no longer counted locally (only accepted requests and 429s), and 503s are retried at most
    twice (30 s, then 60 s). Finding: after midnight Pacific Google itself answered 429 "per day" for Flash, so
    Google did count last night's 503 attempts toward the 20 a day. The local counter may now under-count on an
    overloaded day; Google's 429 still stops the run cleanly.
12. **Flash checks dropped.** The extraction comparison (`compare_extraction.py --provider gemini`) and the Flash half
    of the complexity comparison failed twice (503s on 2026-10-13/14, then Google's daily 429). Dropped as decided.
    Partial result: 4 of the 6 judged complexity items were labelled by Flash; all 4 match Flash-Lite ("meets").
13. **Job-board API terms (specs/15 §2).** Greenhouse, Lever and Ashby document their job-board GET endpoints as
    public and unauthenticated, built for careers pages. None publishes a read rate limit or a separate terms page
    for third-party aggregation. The fetchers read each board once per weekly fetch, with an identifying User-Agent,
    and stop a board on 429. Please confirm this use is acceptable before adding real companies.
14. **Market thresholds are proposals.** REQ_SHARE 0.5, PREF_SHARE 0.2, MARGIN 0.05, MIN_COMPANIES 8, MIN_POSTINGS 20,
    equal tier weights. They are not tuned on any data.
15. **Synonym map location.** The spec names `data/market_synonyms.json`, but `data/` is gitignored (databases). The
    reviewed map lives in `config/market_synonyms.json` so approvals are versioned.
16. **Baseline scores don't apply to demo jobs (specs/17).** Before, a real-judge run on a role baseline also applied
    the candidate to every job using that baseline (the 3 seeded demo jobs). Onboarding scores no longer do: a
    candidate applies only with Apply. Demo tools keep the old behaviour. Please confirm.
17. **Thin profiles (score < 10).** Shown as-is with "Most of your score is missing evidence, not weak evidence."
    No scoring change.
18. **Recruiter access to work authorization/sponsorship answers when assisted apply is built** [OPEN]. For now all
    application details are hidden from recruiters (specs/17 §3.2, confirmed in the Phase A review).
19. **"Not moving forward" waits for the email (specs/17 §7).** The candidate sees it once the decision email is
    approved and sent; before that, the stage the application was in. Please confirm.
20. **Production cost of early start for abandoned onboardings** [OPEN]. Role-specific judging starts as soon as the
    documents are read (specs/18 §2 item 7), so an onboarding that is never confirmed still spends its judge calls.
    Accepted for the demo; to decide before production (e.g. start only after Review opens, or cap per day).
21. **README description and bullets (specs/18 item 22).** Decided in the Phase 3 review: "what was built" blocks →
    description (GitHub description first, ≤1,500 characters); "result/outcome" blocks → bullets, one per sentence,
    list item or table row (a table's header row is skipped), verbatim; language claims capped at the top 3.
22. **Shadow mode makes no calls by default** — my interpretation; please confirm. With `PARSE_SHADOW_CALLS=0` the
    shadow résumé parser asks neither Jev (paid) nor the small claims call (free-tier quota the real run needs);
    those names are counted, not asked. Turn it on to measure the full route.
23. **Gate basis offline** — my interpretation; please confirm. (a) The 16 test profiles never had an LLM
    extraction: the comparison is against today's demo profiles (exact structured parse), with judgments replayed by
    input; a control rebuild must reproduce the recorded score first. (b) Their LinkedIn sections were rendered in
    LinkedIn's export layout keeping all information (every skill under Top Skills; issue date and credential ID
    under each certificate), which a real export does not show. (c) Gate 26 counts an extra item as allowed when it
    is verbatim in the source ("no extra items that aren't in the source").
24. **Early Review merges in the usual order.** A source finished early waits for the ones before it (resume,
    LinkedIn, repos, portfolio), so the final profile is identical to today's. Deleted items stay deleted (Phase 3
    review): matched by normalized name (roles by the merge's similarity rule), shown as "LinkedIn also mentions X ·
    Add back". The memory lasts until the next upload.
25. **Award sentences in a summary** are a parser rule only; the shared extraction prompt was not changed in
    spec 18 (confirmed in the Phase 3 review).
26. **Skill vs tool for unknown names** uses a code heuristic (display only; kind changes no score). The Jev Choice
    for unknowns (item 19) is not built.
27. **Table PDFs fall back** (Phase 3 review): a resume PDF laid out as a table (dates in their own cell) goes to the
    LLM, as do a project title wrapped onto a second line, a header that reads like a sentence, bullets with no role or
    project above them and any item not found in the source. DOCX tables are still parsed (14 of 16 correct, 0 wrong).
28. **Replays prefer the candidate's own answers.** The same text judged for two candidates can get different answers
    (a low-confidence Jev label falls back to an LLM); DS4 replayed 74.7 instead of 74.8 for that reason. Fixed.
29. **Reuse saved answers for identical text across candidates for job-independent checks (consistency vs hiding
    model variance)** [OPEN]. Today a saved judgment is reused only for the same candidate and item. The same text
    judged for two candidates can get different answers: DS4 and DS4_padded share a project whose evidence-quality
    check 4 Jev answered below the threshold both times (0.28 and 0.24); the Flash-Lite fallback said "yes" for DS4 and
    "no" three minutes later for DS4_padded, so identical text scored differently. Reusing the first answer makes scores
    consistent but hides that the model is unsure; re-asking shows the variance. To decide.
30. **README build steps become bullets** — decided (Phase 3 review 3). In a
    "what was built" section, a numbered bold step ("**1. Data processing:** …") is one bullet, and a list whose items
    all open with a past-tense action ("Built…", "Used…") becomes one bullet per item; feature and component lists
    ("Weather Forecast: Fetches real-time weather.") stay in the description. A lead-in ending in ":" is never a
    bullet. On the live profile: 17 bullets (14 before; the LLM writes 38, mostly by rewording prose, which the
    verbatim parser does not do).
31. **Proof density depends on where text lands: repo description counts as artifact (1.0), README bullets as bullet
    (0.5)** [OPEN]. Found in the README gate (Phase 3): the README parser put "what was built" text in the repository
    description and proof density rose from 0.407 to 0.613 on the same evidence. PARSE_README is parked (off); the
    parser code and the recorded gate data (data/readme_gate_jev.json, data/readme-gate-live.db) are kept.
32. **Parked flags (spec 18 wrap-up; code kept, flags off).**
    - `PARSE_README`: see 31; claims 93 vs 132 (LLM), bullets 17 vs 38; AI Engineer +0.98, APM job +1.81 vs a
      partitioned control (fails ±1).
    - `PARSE_RESUME` (shadow, no shadow calls): stress set 0 wrong after tightening (table PDFs always fall back).
    - `PARTITIONED_BATCHES`: labels drifted in both directions (23 changes on the 16 profiles: 18 up, 5 down); the
      keyword-stuffer and padded profiles gained with unchanged evidence (AI2 +2.5, DS4_padded +1.5, PM2 +0.6) — fails
      the no-gain gate (item 33's gate).
    - `GROUPED_ITEM_JUDGING` (complexity only): 15 complexity labels changed in both directions; PM2 +2.25 with
      unchanged evidence — fails the no-gain gate.
    - `GEMMA_JUDGE`, `GROQ_JUDGE`: built, not compared live. Gemma 4 31B answered but returned many HTTP 500/503s.
    - `REPO_CAP` (MAX_REPOS 8): untested on a profile with more than 8 repositories (the test profiles have at most 4).
33. **Judge labels are sometimes generous with loosely related quotes, in the current batched route too** (e.g.
    "Formulated and statistically validated 30+ business hypotheses" credited for Prioritization). Candidate fixes:
    quote-source check, result check against outcome labels, Jev verification, a human-labelled gold set to measure
    agreement [OPEN, for spec 19]. Not built now (no QUOTE_VALIDATION flag).
34. **Permanent acceptance gate** (decided): in every on/off comparison (`scripts/parser_gates.py`,
    `scripts/phase2_compare.py`), a keyword-stuffer profile (ID 2) or a padded variant must not gain from a change unless
    its own evidence text changed; it is part of the DESIGN checks.

