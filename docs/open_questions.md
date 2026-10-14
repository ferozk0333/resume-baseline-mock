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

9. **On-paper floor for the all-bullets route** (specs/13 §0.4). A criterion matched only in the skills list now
   goes to the all-bullets judge. In the re-score, Flash-Lite labelled two tools "missing" although it saw them in
   the skills list: AI1 Git and DS3 SQL went from listed to missing (Tools 0.847 → 0.736 and 0.528 → 0.444). A code
   floor ("named in the skills list" ⇒ at least keyword/listed) prevents this, but it changes three FakeJudge
   archetype scores (up to +0.5), so it is not applied. Apply it and re-record the archetype snapshot?
10. **Lenient bullet credit for a keyword-heavy profile.** PM2 gained "bullet" for Prioritization ("align on
    priorities and roadmaps"), Product analytics ("data-driven decision making") and Cross-functional
    collaboration: final 45.0 → 46.8. Accept, or tighten the definition of "bullet" in the judge prompt?
11. **Failed Gemini attempts count toward the local daily limit.** HTTP 503 (overloaded) retries are counted
    against the 20 Flash requests a day, so one overloaded document can use the whole day's budget. Stop counting
    503 attempts locally? (Whether Google counts them is not documented.)
