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
