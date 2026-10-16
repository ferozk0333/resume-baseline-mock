# 04 — Scoring Rules (source of truth)

Source: *Evidence Extraction & Scoring Pipeline* doc (team formulas) + decisions agreed in planning. Items marked **[DECISION]** were agreed in planning; items marked **[OPEN]** are not settled — implement as written and keep them configurable.

`logic_version = "v0.1"`

## 1. Shared definitions

**Relevance label** (judged once per role and per project, per baseline):
`relevant = 1`, `adjacent = 0.5`, `unrelated = 0`.
Bullets inherit the relevance of their parent role or project.

**Entry-level baseline:** `baseline.required_years <= 2`. **[DECISION]** This rule alone decides entry level; `baseline.level` is a descriptive label and is not used in scoring. The Baselines form warns when they disagree (`entry` with > 2 years, or any other level with ≤ 2 years).

**Today:** `date.today()`, overridable in config for tests.

**Month counting [DECISION]:** all date math is at month granularity (day of month ignored).
- Role length counts both the start and end month: Jan–Dec of one year = 12 months; a role that starts and ends in the same month = 1.
- A current role ends in the current month.
- Recency gap = number of months from the end month to the current month (ended this month → 0; ended in the same month last year → 12).

**missing vs scored 0 [DECISION]:** `missing` means there was nothing to judge, and always comes with guidance. Judged evidence that earns 0 is `scored` with no guidance. Exceptions stated below: Recognition, Validation strength and Scope of impact are `missing` whenever nothing qualifies.

**Subcategory status** (stored in `scores.status`):
| Status | Meaning | Score | In averages? | Guidance shown? |
|---|---|---|---|---|
| `scored` | Evidence found and judged | 0–1 | yes | no |
| `missing` | Evidence could exist but was not provided or found | 0 | yes | **yes** |
| `na` | The subcategory cannot apply to this profile | NULL | no | no |
| `error` | A model call failed after retries | NULL | no | shows error |

**Required/preferred combination** **[DECISION]** (Role-specific skills, Applied knowledge, Tools):
```
combined = (2 × R + P) / 3
if P is N/A (baseline has no preferred items): combined = R
if R is N/A (baseline has no required items):  combined = P
if both N/A: status = na
```

## 2. Experience

### Relevance
```
months(role)    = months between start and end (end = today if current)
Relevance       = Σ(months × relevance) / Σ months      over roles
```
No roles → `missing` (guidance: add work experience).
Example: 24 relevant + 12 adjacent of 36 months → (24 + 6) / 36 = 0.83.

### Depth
```
Build a month-by-month timeline of roles. For overlapping months, take the highest relevance in that month (count each month once).
Relevant years = Σ(month relevance) / 12
required       = max(1, baseline.required_years)        # 0 treated as 1
Work_score     = min(1, Relevant years / required)

Project_score  = min(1, Σ(project relevance) / P)        # P = 3 (config)
                 projects, hackathons, publications, case studies each count as one;
                 GitHub repos count only if not (is_fork and own_commits == 0)

Entry-level baseline:   Depth = max(Work_score, Project_score)
Otherwise:              Depth = Work_score
```
Nothing to count → `missing`.

**[OPEN]** `NO_ROLES_RELEVANCE_SCOPE_NA` (config, default **off**): when on and the profile has no roles at all, Relevance and Scope are `na` instead of `missing`. Measured on the Project-strong grad archetype (Data Scientist, entry): off → final 61.9, Experience 0.38; on → final 80.0, Experience 0.99.

### Scope
Judge compares the **most recent role with relevance ≥ 0.5** (prefer `relevant` over `adjacent`) to `baseline.scope_expectation`:
`meets = 1`, `partly = 0.5`, `below = 0`.
No such role → `missing`.

### Progression
Judge: "Have responsibilities become more impactful in the most recent role compared with previous ones?"
`more_impactful = 1`, `same = 0.5`, `less = 0`.
Fewer than 2 roles → `na`.

### Recency
```
Eligible activities:
  entry-level baseline → roles, projects, publications, hackathons
  otherwise            → roles only
gap_years     = months from end date to today / 12      (0 if ongoing)
activity      = relevance × 0.9 ^ gap_years             # 0.9 = config DECAY
Recency       = max(activity) over eligible activities with relevance > 0
```
None eligible → `missing`.
**[OPEN]** Whether GitHub repos should count as Recency activities for entry-level baselines (using the last commit date). Until decided, repos and case studies do **not** count.

## 3. Expertise

Baseline criteria come from `baseline_criteria` (kind = skill or tool). Each item is classified as **skill or tool, never both**. Criteria of kind `certification` are not scored here; they define which certifications count as "relevant" for Validation strength and are passed to the improvement plan.

### Role-specific skills
Per baseline skill, best evidence:
`bullet = 1` (skill shown inside a role/project bullet or case study), `keyword = 0.5` (named only, e.g. skills list), `missing = 0`.
```
R = Σ(required skill scores) / #required
P = Σ(preferred skill scores) / #preferred
Role-specific skills = combined (section 1)
```

### Applied knowledge
Per baseline skill, best evidence:
`skill_task_result = 1`, `skill_task = 0.5`, `mention_or_missing = 0`.
Same R / P / combined pattern. (Team doc defines required only; preferred added by **[DECISION]** on the 2:1 combination.)

### Technical / tools proficiency
Per baseline tool, best evidence:
| Evidence | Score |
|---|---|
| Used in a role, project, work sample, or counted GitHub repo | 1 |
| Certification for the tool | 1 **[OPEN: team doc says 1; earlier proposal was 0.5 if unverifiable. Keep as config `UNVERIFIED_CERT_TOOL_SCORE = 1.0`]** |
| Listed only | 0.5 |
| Missing | 0 |
Same R / P / combined pattern.

### Complexity
For each role/project with relevance > 0, the reasoning model grades against `baseline.complexity_criteria`, reason first:
`meets = 1`, `partly = 0.5`, `no = 0`.
```
Complexity = max over graded items
```
No relevant items → `missing`.

### Work samples / artefacts
Yes/no for each: relevant GitHub repo (counted repos only), relevant portfolio project, relevant presentation, relevant publication.
```
Work samples = 1 if any yes, else 0
```
None of the four exist at all → `missing` (guidance: add GitHub, portfolio, or work samples).

## 4. Authority

All per-bullet labels below are **job-independent** (stored with `baseline_id = NULL`) and multiplied by the bullet's inherited relevance at scoring time.

### Outcomes **[DECISION: multiplied by relevance]**
Per bullet: `has_metric` (yes/no), `has_direction` (yes/no).
```
level   = 1 if both, 0.5 if exactly one, 0 if neither
Outcomes = max(level × relevance) over bullets
```

### Impact
Per bullet: `business = 1`, `operational = 0.5`, `none = 0`.
```
Impact = max(level × relevance) over bullets
```

### Ownership
Per bullet: `led = 1`, `contributed = 0.5`, `not_stated = 0`.
```
Ownership = max(level × relevance) over bullets
```

### Scope of impact
Per bullet reach: `own_work = 1`, `team = 2`, `cross_functional = 3`, `organization = 4`, `external = 5`, `not_stated`.
```
Candidate reach = max reach over bullets whose parent relevance > 0     [OPEN: relevance filter applied by analogy with Outcomes; config SCOPE_OF_IMPACT_RELEVANCE_FILTER = true, set false to count all bullets]
gap             = baseline.expected_reach − candidate reach
Scope of impact = 1 if gap <= 0; 0.5 if gap == 1; 0 if gap >= 2
```
No reach stated in any counted bullet → `missing` (with the filter on, reach stated only in unrelated bullets is `missing`).

**[OPEN] Observation:** Authority has no time decay (only Recency does). The Stale expert archetype scores Authority 0.90 from bullets 5–8 years old. Kept as written.

### Recognition
Yes/no: role-relevant professional award; publication in a relevant field.
```
Recognition = (award + publication) / 2
```
Neither present → `missing` (score 0, guidance).

## 5. Trust (job-independent; computed once per candidate and reused)
**[DECISION, phase 5]** Proof density, Consistency and Evidence quality are job-independent: their judgments are stored with `baseline_id = NULL` and reused across baselines with no new model calls. **Validation strength is job-dependent**: whether a reference confirms *relevant* work (`reference_confirms`, stored with `baseline_id` + `baseline_hash`) and whether a certificate is *relevant* (matched against the baseline's certification criteria) depend on the role. All four are recomputed per run from saved judgments.

### Proof density
For each distinct claim (skills, tools, outcomes; deduplicated by `name_norm`), best support:
`artifact = 1` (separate repo, case study, dashboard, certificate shows it), `bullet = 0.5`, `claimed_only = 0`.
```
Proof density = Σ levels / #claims
```
No claims → `missing`.

### Validation strength
```
1   if a reference with relationship manager/supervisor confirms relevant work
0.5 if a verified relevant certification exists   (verified = credential_id or verify_url present; MVP rule)
0   otherwise
Validation strength = highest level present
```
None → `missing` (guidance: add a reference or a verifiable certification).

**Relevant certification [DECISION]:** matched in code, case-insensitively, when the baseline certification criterion's name or one of its aliases appears as a whole phrase in the certificate title. Baseline names are generic ("Cloud ML certification"), so real titles usually match only through aliases. An unmatched certificate is recorded in `details` as `not_matched` (not "irrelevant") and does not count. *Proposal, not yet confirmed:* a yes/no judge question `certification_relevant` as a fallback for unmatched certificates.

### Consistency
For each non-resume source present (LinkedIn PDF, GitHub, portfolio, work samples), judge whether it aligns with the resume (skills, projects, experience, dates): `aligned` / `not_aligned`.
```
Consistency = 1 if any source is aligned, else 0
```
No second source → `missing` (guidance: add LinkedIn PDF, GitHub, or portfolio).

### Evidence quality
For each role and project, 5 yes/no checks: names tools/methods; includes numbers; includes dates/timeframe; describes context; states the candidate's own role.
```
item score       = yes / 5
Evidence quality = average item score
```

### Integrity
Out of scope. Not computed, not shown.

## 6. Weights and aggregation

Default weights from the team's subcategory table. Integrity's 5% is redistributed across the other Trust subcategories in proportion to their weights **[DECISION]**.

| Dimension | Weight | Subcategory | Weight in dimension |
|---|---|---|---|
| Experience | 30% | Relevance | 35% |
| | | Depth | 25% |
| | | Scope | 20% |
| | | Progression | 10% |
| | | Recency | 10% |
| Expertise | 30% | Role-specific skills | 30% |
| | | Applied knowledge | 30% |
| | | Technical / tools proficiency | 15% |
| | | Complexity | 15% |
| | | Work samples / artefacts | 10% |
| Authority | 20% | Outcomes | 30% |
| | | Impact | 30% |
| | | Ownership | 20% |
| | | Scope of impact | 10% |
| | | Recognition | 10% |
| Trust | 20% | Proof density | 30/95 = 31.58% |
| | | Validation strength | 30/95 = 31.58% |
| | | Consistency | 25/95 = 26.32% |
| | | Evidence quality | 10/95 = 10.53% |

```
Dimension score = Σ(w_s × score_s) / Σ(w_s)     over subcategories with status scored or missing
                  (na and error excluded; weights renormalize)
Final score     = 100 × Σ(W_d × dim_d) / Σ(W_d)  over dimensions with at least one included subcategory
```
- Weights are stored in config, snapshotted into `runs.weights_json`, and editable in the UI sidebar (whole numbers, step 1; only ratios matter after normalization). Each group is normalized to 100% before use.
- Changing weights re-aggregates saved subcategory scores; it never triggers model calls.
- A weight group whose weights are all 0 contributes nothing. If every subcategory weight in a dimension is 0, that dimension is excluded from the final score and the other dimensions renormalize.
- No score labels ("strong", "moderate") are shown — thresholds are not defined.

## 7. Gap ranking (code, before the LLM report)

```
effective weight of s = (W_d / ΣW) × (w_s / Σw in d)      # after N/A renormalization
potential gain (points) = 100 × effective weight × (1 − score_s)
```
- Rank subcategories with status `scored` or `missing` by potential gain, descending.
- `na` subcategories are never gaps.
- Pass the top 5, with their details (criteria not met, missing evidence, winning/losing items), to the improvement plan (`05` §C). Subcategories already at 1.0 (gain 0) are not gaps.
- The gain is the maximum possible if the subcategory reached 1.0. Show it as "up to N points", never as a promised score.

## 8. Worked check (use as a unit test)
Candidate: one current relevant role of 24 months, no projects; baseline required_years = 2 (entry level).
- Relevance = 1.0; Depth = max(Work_score = min(1, 2/2) = 1.0, Project_score = 0) = 1.0; Recency = 1.0; Progression = na.
- Experience = (35×1 + 25×1 + 20×Scope + 10×1) / (35+25+20+10) with Progression excluded.
- If Scope = 0.5: Experience = (35 + 25 + 10 + 10) / 90 = 0.889.
