# 09 — Speed

Today a full run (upload → score → plan) takes 2–3 minutes. Target: **about 30 seconds from "Confirm & score" to the score on screen**, with the improvement plan arriving shortly after. Scores must not change.

## 1. Measure first
- Add timing for every pipeline step: each ingest source, each extraction call, each graph node, each judge call (question key, model, method, latency, input tokens), aggregation and the plan.
- Store a per-run summary in `runs.timings_json` and print a table at the end of each run (step, calls, total seconds, share of run).
- Run it on my resume (+ LinkedIn PDF + GitHub) **before any change** and include the table in the report. It shows where the time actually goes; use it to order the work below.

## 2. Changes (apply in this order; re-measure after each)

**2.1 Run judge calls concurrently.**
- Use async calls with a concurrency limit (`JUDGE_CONCURRENCY`, default 8) across all four dimension subgraphs, not just between them.
- On 429/529 or rate-limit errors, back off and retry as today. The limit is shared by Jev and Claude calls.

**2.2 Batch the unmatched-skill calls.**
- Today each skill/tool with no alias match sends every bullet to Claude in its own call.
- Instead: **one Claude call per question family** (`skill_evidence`, `applied_level`, `tool_evidence`) per candidate per baseline, covering all unmatched criteria. Output: a list of `{criterion, label, quote}`.
- Keep every existing rule: labels above `missing` need a quote found in the input; a criterion that fails the check is retried **alone** once, then `missing` with the reason recorded.
- Each criterion is still saved as its own judgment row.

**2.3 Several questions per Jev request.**
- Jev accepts many questions in one request (confirmed in the phase 4 probe).
- Per bullet: send `outcome_metric`, `outcome_direction`, `impact_type`, `ownership` and `reach` in one request.
- Per role/project: send the 5 `quality_check` questions in one request.
- Each answer is still saved as its own judgment row with the same cache key as today, so saved judgments stay valid. Fallback to Claude stays per question.

**2.4 Prompt caching for repeated candidate context.**
- Where the same long candidate text is sent in several Claude calls (e.g. the full bullet list), put it in a cached prefix. Check the current Anthropic docs for the exact syntax and minimum cacheable length before coding.

**2.5 Extract sources in parallel.** Resume, LinkedIn PDF, portfolio and README extraction calls run concurrently.

**2.6 Start early, finish late.**
- **Start early:** as soon as extraction and validation finish, begin the **job-independent** judgments (Authority per-bullet labels; Proof density, Consistency, Evidence quality) in the background while the candidate is on the review screen. Items the candidate edits are re-judged automatically (the text-match cache already handles this).
- **Finish late:** return the score as soon as aggregation is done. Generate the improvement plan afterwards with status `generating`; the UI shows it when ready.

## 3. Constraints
- FakeJudge archetype scores are identical before and after (regression test from `ITERATION_2.md` rule 1).
- The cache key of every judgment type except the batched unmatched-skill route is unchanged. Batched results for that route are new rows; note this in the report.
- No change to labels, options, prompts' meaning or the scoring formulas. Prompt *structure* may change for batching; keep the same instructions and options.

## 4. Tests
- Concurrency: a FakeJudge with artificial delay proves calls overlap (total time ≈ longest call × batches, not the sum).
- Batching: one call for N unmatched skills; a failed quote for one criterion triggers a solo retry for that criterion only.
- Jev multi-question: one request carries N questions; each answer saved as its own row; a low-confidence answer falls back alone.
- Early start: editing a bullet during review causes exactly that bullet to be re-judged.

## 5. Report
Before/after timing tables, number of model calls before/after, and the archetype regression result.
