# 14 — Create a job (recruiter) — v2

Replaces the earlier 14. A recruiter creates and publishes a job in **4 steps**, from a job description (JD) file, pasted text, a copy of an existing job, or by hand. A published job appears in **Open jobs** and candidates can apply. Each job owns its own baseline behind the scenes; the word "baseline" never appears in the recruiter UI. Scoring logic does not change.

## 1. Recruiter problems this must solve
| Problem | Design answer |
|---|---|
| Retyping a JD that already exists | Upload/paste → fields filled automatically, each marked "From JD" with the source sentence on hover |
| Long forms, lost work | 4 steps, autosave every change, "Save as draft" everywhere, resume where they left off |
| JDs are wish lists (everything "required") | Must-have toggle separate from required/preferred; warning above 12 required items |
| Unsure how settings affect ranking | One-line plain explanation next to each setting; live preview of the candidate-facing posting |
| Recreating similar jobs | "Copy an existing job" |
| Changing a live job breaks things | Edit warning with the number of applicants to re-score |
| AI extraction may be wrong | Everything is editable; extraction never blocks; manual path always available |

## 2. Jobs page
- Tabs **Open · Drafts · Closed**; columns: title, company, location, applicants, created, status.
- Primary button **Create job** (top right). Row menu: Edit, Duplicate, Close/Reopen.
- The three seeded demo jobs stay, as Open.

## 3. The 4 steps
Stepper at the top; Back / Next at the bottom; "Save as draft" always visible; autosave on every change.

**Step 1 — Start**
Three cards:
- **Upload or paste a job description** (PDF, DOCX or text) → "Read job description" → fills steps 2–3 → opens step 2.
- **Copy an existing job** (any job or seed template) → opens step 2 prefilled.
- **Start from scratch** → opens step 2 empty.

**Step 2 — Role & requirements**
- Details: title*, company*, department, location, workplace (on-site / hybrid / remote), employment type, level* (entry / mid / senior / lead), minimum years*, short description shown to candidates.
- Requirements table: name, type (skill / tool / certification), required or preferred, **must-have** toggle, aliases (chips, "Suggested" when extracted), short description. Add, delete, drag to reorder.
  - From a JD: "Minimum/Required" → required; "Preferred/Nice to have" → preferred; lists split with the shared rules.
  - Must-haves pre-ticked for the first four required items.

**Step 3 — Hiring setup**
- *What good looks like* (plain questions mapped to scoring fields):
  - "What will this person own?" → `scope_expectation`
  - "What makes the work hard?" → `complexity_criteria`
  - "How far does their work reach?" → `expected_reach`: own work · team · cross-functional · organization · external
- Ranking emphasis: preset picker (team table by default); dimension sliders under "Advanced".
- Interview plan: default stages from spec 16, editable; interview duration; recruiter name; availability windows or scheduling link.

**Step 4 — Review & publish**
- Left: summary with "Edit" per section. Right: **candidate preview** of the posting.
- Non-blocking warnings: no required items; more than 12 required items; level and years don't fit; empty "What good looks like".
- **Publish** → job is Open and lands on its job page ("No applicants yet"). **Save as draft** keeps it in Drafts.

## 4. Reading a job description (model role `JOB_EXTRACT_MODEL`, configurable)
- Normalize the file with the existing document normalizer, then one structured-output call returning details, requirements (name, type, priority, aliases, quote), responsibilities, years and level, and drafts for scope, complexity and reach (each with a quote).
- Same rules as candidate extraction: JD text is data (inside tags, instructions in it ignored), quote check on every item, nothing invented.
- Code normalizes years ("3+ years" → 3; "2–4 years" → 2; "new grad" or "0–1" → 0) and maps the level.
- Salary, benefits, EEO statements, team size and "how to apply" are never requirements.
- Fallback when the model or its quota is unavailable: step 2 opens empty with the JD text in a side panel to copy from, plus a notice. Creation is never blocked.

## 5. Data
- New job = a job-owned baseline (same schema) + a `jobs` row: status (`draft` / `open` / `closed`), details, interview plan, weights, must-haves, original JD text and file.
- The job page gets a **Job description** tab.

## 6. Candidates can apply
- Open jobs appear on candidate Home ("Apply to a job").
- Applying uses the candidate's confirmed profile, scores against that job (job-independent judgments reused), and creates an application visible to the recruiter.
- Closed jobs disappear from the candidate side; existing applications stay.

## 7. Editing a published job
- Requirement or "What good looks like" changes alter the baseline hash. Before saving: "N applicants will be re-scored. About M model calls." → **Save and re-score** / **Cancel**.
- Detail, interview plan or weight changes need no model calls.

## 8. Tests and report
- Tests: year parsing and level mapping; JD extraction validation on a fixed JD fixture with the fake model (quote check, splitting, required vs preferred, benefits not extracted); fallback path; autosave and resume; publish → Open jobs and candidate Home; apply → recruiter list; edit → re-score warning; Playwright for upload, copy and scratch paths.
- Report: screenshots of each step and the Jobs page; model calls used; one real extraction of `AI_Engineer_JD_Brightwater.pdf` (provided) next to its expected results:
  - minimum years 1, level entry;
  - "Vector databases (pgvector, Pinecone or Weaviate)" → pgvector, Pinecone, Weaviate;
  - "AWS (SageMaker, Bedrock) or GCP (Vertex AI)" → AWS, SageMaker, Bedrock, GCP, Vertex AI;
  - "Hyperparameter tuning (Bayesian, grid or random search)" stays one item;
  - "Docker and basic CI/CD" → Docker, CI/CD;
  - one preferred certification with both certificate names as aliases;
  - reach: cross-functional;
  - no salary, benefits, EEO, team size or "how to apply" items.
