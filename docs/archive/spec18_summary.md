# Spec 18 summary: speed, failover, code-first extraction, injection screen

Branch `feature/speed-failover`, merged into `main` and tagged `v0.2-speed`. Scoring formulas, weights and
`logic_version` v0.1 are unchanged, the archetype regression is identical and the Jev confidence threshold is unchanged.

## What is on

| Feature | Default | What it does |
|---|---|---|
| Model routing with failover and fail fast | on | Ordered model lists per task (`ROUTE_*`); a 429, 5xx or timeout moves to the next model at once; every model out of quota → "Daily free-tier limit reached…" (saved work is kept). Per-model limits; provider answers win. |
| Capability table (`MODEL_CAPS`) | on | A request sends only what the model accepts (Gemma: no thinking setting; newer Claude models: no temperature). |
| Early start | on | Role judging starts as soon as the resume is read and roles are picked; Confirm only finishes what is left. |
| Early Review with sticky deletions | on | Review opens once the resume is read ("Still reading LinkedIn…" per source; Confirm waits). Each source is saved as it is read, in the usual order (same final profile). A deleted item is never re-added: "LinkedIn also mentions X · Add back". |
| `PARSE_LINKEDIN` | on | LinkedIn "Save to PDF" read in code (≈10 ms, no Jev, no LLM); anything the confidence gate rejects goes to the LLM. |
| `REMOVE_HIDDEN_TEXT` | on | White or near-background, tiny (< 4 pt) and off-page text is removed before reading; kept for Admin. |
| `PROMPT_GUARD` | on (calls Groq only with `LIVE=1`) | Llama Prompt Guard 2 (86M, 22M overflow when 86M is busy for the minute) screens documents, READMEs and market postings in the background through the shared limiter. Never blocks, never changes a score, never shown to recruiters; flagged hidden text gives the candidate "We ignored hidden text in your …". Admin → Screening. |
| Live guard | on | Real provider calls only with `LIVE=1` (`make live`). |

## What is parked (code kept, flag off)

| Flag | Why |
|---|---|
| `PARSE_README` | Proof density depends on where text lands (repo description = artifact 1.0, README bullets = bullet 0.5): 0.407 → 0.613 on the same evidence. Claims 93 vs 132, bullets 17 vs 38. AI Engineer +0.98, APM job +1.81 vs control (fails ±1). |
| `PARSE_RESUME` (shadow) | Kept in shadow without calls. Stress set after tightening: 0 wrong (see below). |
| `PARTITIONED_BATCHES` | Labels drifted both ways (23 changes on 16 profiles); stuffer/padded profiles gained with unchanged evidence (AI2 +2.5, DS4_padded +1.5, PM2 +0.6): fails the no-gain gate. |
| `GROUPED_ITEM_JUDGING` (complexity only) | 15 complexity labels changed both ways; PM2 +2.25 with unchanged evidence: fails the no-gain gate. 93 single calls → 13 batched. |
| `GEMMA_JUDGE`, `GROQ_JUDGE` | Built (calls take turns), not compared live. |
| `REPO_CAP` (`MAX_REPOS` 8) | Untested on a profile with more than 8 repositories. |

Open questions 31–34 record these, the generous-quote issue (spec 19) and the permanent acceptance gate.

## Measured numbers

**Gates (offline, replayed judgments)**
- LinkedIn parser: real LinkedIn PDF matches today's LLM extraction (4/4 roles, 30/30 claims, 3/3 certifications, dates
  8/8, proof density equal). 16 test profiles: every final identical (Δ 0.00), ranking 1 > {3,4} > 2 > 5 in all three
  roles, all DESIGN checks pass, 13/13 LinkedIn-style exports parsed correctly.
- Resume shadow parser, layout stress set (correct / fell back / wrong): 1-column PDF 15/1/0, 1-column DOCX 15/1/0,
  2-column PDF 13/3/0, 2-column DOCX 15/1/0, table PDF 0/16/0, table DOCX 14/2/0.

**Live comparisons (16 synthetic test profiles, own-role baselines)**
- `PARTITIONED_BATCHES` + `REPO_CAP`: score changes −1.0 to +2.5; ranking and DESIGN checks pass; 154 grouped calls
  (Flash-Lite 3.5 91, 3.1 51, Gemma 31B 12) + 12 single; 429 s.
- `GROUPED_ITEM_JUDGING`: only PM2 moved (+2.25); 13 batched calls; 26 s.
- README gate on the saved profile (two runs): reading replays from `data/readme_gate_jev.json`; run 2 judging 736 s,
  no daily limit, 0 labels changed on identical input.

**Prompt Guard**: 77 documents, 110 chunks, 110 calls; 0 flagged (highest 0.033, agentic/RAG READMEs); a synthetic
injection scored 0.999. Response format confirmed: the score as the message text. 512-token window; a 1,200-character
chunk ≈ 300 tokens.

**Speed**: a live onboarding read (resume, LinkedIn, 23 READMEs) took 2 min 19 s and scoring one role about 1 min 47 s
(2026-10-16). With `PARSE_LINKEDIN` on, LinkedIn costs no model call; Review opens as soon as the resume is read.

## Free-tier limits (configured; provider answers win)

| Route / model | RPM | TPM | RPD | TPD |
|---|---|---|---|---|
| extract_document, plan: Gemini 3.8 / 3.7 / 3.6 Flash, 3 Flash preview | 5 | 250K | 20 each | — |
| extract_web, judge_single, judge_grouped: Gemini 3.5 / 3.1 Flash-Lite | 15 | 250K | 500 each | — |
| Gemma 4 31B (overflow; 26B removed: not found on v1beta) | 30 | 16K | 14.4K | — |
| Groq gpt-oss-120b, gpt-oss-20b, qwen3.8-27b (judge_single overflow, extract_web overflow) | 30 | 8K | 1,000 | 200K |
| Groq Prompt Guard 2 86M / 22M | 30 | 15K | 14.4K | 500K |
| Jev (paid) | — | — | — | — |

What binds: resume reads and plans share four Flash models at 20 requests a day each (≈80 resume reads or plans a day);
judging uses Flash-Lite (≈1,000 requests a day across both) with Groq and Gemma as overflow. Used on 2026-10-17 (Pacific
day) by the gates and comparisons: Flash-Lite 3.5 286/500, 3.1 154/500, Groq 120b 35, qwen 19, 20b 10, Gemma 31B 11,
Prompt Guard 86M 60 and 22M 50.
