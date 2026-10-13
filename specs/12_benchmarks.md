# 12 — Benchmarks: percentiles and "what higher scorers have"

Give the score a reference point, and tell candidates what higher-scoring candidates for the same role typically have, without exposing anyone's data.

## 1. Percentiles
- **Pool:** the latest run of each candidate against the **same baseline**, with the same `logic_version` and the default weights. Recruiter view: the job's applicants, with the job's weights.
- **Value:** share of the pool with a lower score (ties count half). Computed for the final score and each dimension.
- **Display:** "Higher than 72% of 140 candidates scored for Data Scientist". Always show N. Keep the absolute score next to it.
- **Minimum pool:** show percentiles only when N ≥ `MIN_POOL` (config, default 20). Below that, show "Not enough candidates for a comparison yet". For the local demo, `MIN_POOL` may be lowered in `.env`; the UI then shows a "demo pool" badge.
- Never show a percentile across different baselines.

## 2. "What higher scorers have" (peer signals)
**Features (computed in code from existing labels, per candidate and baseline):**

| Feature | Source | Dimension |
|---|---|---|
| Has a verifiable relevant certification | Validation strength inputs | Trust |
| Has a manager/supervisor reference confirming relevant work | Validation strength inputs | Trust |
| Has a second source aligned with the resume | Consistency | Trust |
| Share of claims backed by an artifact | Proof density | Trust |
| Links a counted repo or work sample relevant to the role | Work samples | Expertise |
| Required skills shown in work (share) | Role-specific skills | Expertise |
| Has a bullet with a metric and a direction | Outcomes | Authority |
| Has a bullet with business impact | Impact | Authority |
| Has a bullet stating they led the work | Ownership | Authority |
| Relevant months of experience (bucketed) | Depth | Experience |

Keep the feature list in one config file; adding a feature must not need code changes elsewhere.

**Rule:**
1. For each dimension where the candidate is **below the pool median**, take the candidates **above** the median.
2. For each feature of that dimension, compute how common it is in that group, and whether this candidate has it.
3. Show at most 3 features per dimension where the group rate is high and the candidate lacks it, ordered by the group rate.
4. Text is built in code: "Among higher-scoring candidates for this role, 74% link a repo that shows a listed skill. You don't yet."

**Privacy rules:**
- Only counts and percentages. No text, quotes, names, employers, project names or skill lists from other profiles, ever.
- Suppress any statistic computed from fewer than `MIN_GROUP` people (config, default 10).
- Round percentages to the nearest 5.
- Same `MIN_POOL` rule as section 1.

## 3. Where it appears
- Candidate Score page: percentile under each score card.
- Candidate Plan page: under each gap, a "What higher scorers have" line when available. The plan model receives these lines as input and may refer to them, but the numbers come from code.
- Recruiter list: percentile within the job's applicants (optional column).

## 4. Tests
- Percentile maths, ties, and the minimum pool rule.
- Peer signals: correct group selection, suppression below `MIN_GROUP`, rounding, and a test that no string from another candidate's profile appears in any output.
- Percentiles and signals recompute when a new run is saved.

## 5. Report
Screenshots with the demo pool, the feature list, and any feature you could not compute from existing labels.
