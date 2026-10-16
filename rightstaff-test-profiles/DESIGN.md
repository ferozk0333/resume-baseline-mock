# Test profiles — design notes (do NOT show to judges)

All people, companies, schools, credential IDs and URLs are fictional. Certification names are real programs; credential IDs contain `TEST` and are not real.

## Pattern used in every role

| ID | Archetype | What it tests in our pipeline | Expected place |
|---|---|---|---|
| 1 | Strong fit: ~2 yrs relevant role, quantified results, artifacts, verified certification, manager reference, LinkedIn aligned | Top across all four dimensions | 1st |
| 2 | Keyword stuffer: long skills list, vague bullets, no results; LinkedIn contradicts the resume title/dates | Volume gaming fails; Consistency = not aligned; listed-only skills get 0.5 at most | 4th |
| 3 | Project-heavy new grad: no jobs; 3 strong projects, hackathon award, repos or portfolio, supervisor reference | Entry-level project path (Depth, Recency); Relevance/Scope with no roles (`NO_ROLES_RELEVANCE_SCOPE_NA` flag) | 2nd–3rd (open) |
| 4 | Career switcher: 3–5 yrs in an adjacent role, one relevant project | Relevance 0.5, Scope, Progression; reference relevance | 2nd–3rd (open) |
| 5 | Thin: short resume, unrelated job, no links | Many `missing` subcategories with guidance | 5th |

**Expected order by construction:** 1 > {3, 4} > 2 > 5. The 3-vs-4 pair is deliberately open; disagreement there is expected and fine.

## Role-specific notes

**Data Scientist**
- DS2: resume says "Data Scientist" from Jan 2023; LinkedIn says "Data Analyst" from Jun 2023 and an earlier coordinator role. Certification has no credential ID (unverified).
- DS3: has one fork with 0 own commits (must not count). Has a student poster (publication) and a hackathon award (Recognition).
- DS4: reference is a manager, but for marketing work (tests whether it counts as "relevant").
- **DS4_padded**: same facts as DS4 plus fluff (summary, core competencies, generic bullets, interests). No new facts. Used for the length-bias check of the judges, and as a robustness check of our pipeline: its score should be close to DS4's.

**AI Engineer**
- AI2: three forks with 0 own commits plus one tutorial repo; freelance role with no verifiable results; earlier unrelated support job.
- AI4: 5 years of backend engineering (adjacent, strong on APIs/Docker/AWS) with an LLM prototype inside the role. A strong switcher: judges may rank AI4 above AI3, or even near AI1. This is the most interesting pair.
- AI5: no LinkedIn, GitHub or certification.

**Product Manager**
- No GitHub except PM4 (an engineer), whose repo is a spec, not code.
- PM2: resume says "Product Manager"; LinkedIn says "Project Coordinator" (consistency test).
- PM3: has a shipped side product with real usage numbers, a published teardown, a sprint award, and a portfolio.
- PM4: the reference is a **peer** (the PM), so Validation strength should not get the manager credit.

## Blind labels used in the judge packs

| Role | A | B | C | D | E |
|---|---|---|---|---|---|
| Data Scientist | DS2 | DS4 | DS1 | DS5 | DS3 |
| AI Engineer | AI3 | AI1 | AI5 | AI2 | AI4 |
| Product Manager | PM4 | PM2 | PM3 | PM1 | PM5 |

Length-bias pack: Data Scientist forward order, with B = DS4_padded instead of DS4.
