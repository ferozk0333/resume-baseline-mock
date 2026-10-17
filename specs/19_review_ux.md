# Spec 19 — Onboarding & Review UX: one card layout, dedupe, more proof types

Status: approved by Feroz (2026-10-17). Goal: an easier, smoother Review with less friction and no confusing duplicates. The quality work (gold set, quote validation, backed vs claimed, verified references) moves to **spec 20**.

## 0. Product rules
- Scoring formulas, weights and `logic_version` v0.1 unchanged. Phase C changes *inputs* to scoring (merged duplicates) as a bug fix under the existing rule "one claim per name". It must be reported before/after.
- No new model calls, except the existing re-judging of text the candidate edits or adds.
- Every change applies to all candidate-facing forms. Shared form styling (Phase A) also applies to recruiter and Admin screens.
- Accessibility: labels on every input, visible focus, keyboard operable (including bullet reordering), contrast AA, works at 1440×900 and 390 px without horizontal scroll.

## 1. Phase A — Quick fixes
1. **EEO:** the section title is just "Voluntary self-identification". Remove the explanatory paragraph. All answers still default to "Decline to answer". The code rules stay: never sent to a model, never shown to recruiters, never used in scoring.
2. **Upload drop zone (shared component)** for resume, LinkedIn PDF and any other file upload:
   - Empty state: "Drop your resume here or **browse** · PDF or DOCX". The whole zone is clickable and keyboard focusable.
   - After upload: file icon, file name, size, "Replace" and "Remove".
   - Errors in plain words (wrong type, too large; size limit in config).
   - Replaces the browser's "Choose File / No file chosen".
3. **Form styling (shared input component and theme tokens):**
   - Editable inputs: white background with a border.
   - Placeholders: light muted grey, clearly lighter than entered values. Entered values: dark text.
   - Read-only fields (used in Phase B view mode): no input chrome, subtle background, dark text, clearly not editable.
   - Apply everywhere: candidate, recruiter, Admin.
4. **Placeholders:**
   - GitHub: "your-username" (not "octocat");
   - Portfolio: "https://your-site.com";
   - LinkedIn URL: keep "https://www.linkedin.com/in/your-name".
5. **Honest credential labels.** We don't check credential IDs, so don't say "verified":
   - with an ID or link: "Credential ID added" / "Link added";
   - without: "Add an ID or link" (optional).
   - Helper text: "Adding a credential ID or link makes a certificate count as stronger proof (Trust)."
   - Use the same wording on recruiter screens.
   - The scoring rule (credential_id or verify_url → 0.5 in Validation strength) is unchanged; only the wording changes.
6. **Punctuation spacing:** in text normalization, remove whitespace before `. , ; : ! ? )` and after `(`.
   - Apply to **new** extractions only. Don't migrate saved items, because changing saved text would trigger re-judging.
   - The quote check and the "Source text" view use the same normalized text, as today.

## 2. Phase B — Review redesign
7. **One entry card** (shared component) for roles, projects, education and every proof type in Phase D. It is Workday-style: a fixed field layout that becomes editable at the click of a button.
   - **Header (always visible, collapsible):**
     - "Title — Organization";
     - friendly dates: "Oct 2025 – Jun 2026", "Oct 2025 – Present", "2024" when year-only;
     - "5 bullets";
     - source chips (Resume, LinkedIn, GitHub, Portfolio, Added by you);
     - a "Check dates" badge when `needs_review`.
   - **Body:** the **same field grid in view and edit mode.**
     - Roles: Title, Employer, Location (optional), Start (month), End (month) + "Current", Bullets.
     - Projects: Name, Type, Link, Start/End, Related role (optional), Bullets.
     - Education: School, Degree, Field, Start/End, Notes.
   - **View mode:** read-only fields (Phase A style). An "Edit" button (pencil + text) sits top right, plus Delete.
   - **Edit mode:**
     - the same fields become inputs in place; nothing moves;
     - "Save" (primary) and "Cancel";
     - only one card in edit mode at a time;
     - leaving with unsaved changes asks to confirm.
   - **Delete:** confirm, then an "Undo" toast. Deleted items stay deleted when later sources finish (existing rule).
8. **Bullets edited one by one** (replaces the "one per line" textarea):
   - Each bullet is its own field.
   - Delete button per bullet.
   - Reorder with a drag handle **and** up/down buttons for keyboard users.
   - "+ Add bullet" at the end. Pasting several lines into a new bullet splits them into separate bullets.
   - Empty bullets are not saved.
   - Edited and added bullets keep today's behaviour: `source=user_edit`, re-judged at scoring.
9. **Dates:** month pickers; "Current" disables End. Year-only dates show "We only found the year. Please check the months" until edited or confirmed.
10. **Skills and tools:**
    - Grouped under headings: Languages, AI & ML, Data, Cloud & infrastructure, DevOps & tooling, Product & methods, Other. Groups come from a reviewed config file (`config/skill_groups.yaml`, seeded from baseline criteria and the synonym map); unknown names go to "Other".
    - No "tool"/"skill" prefix on chips.
    - Show the first 12 per group with "Show all (n)".
    - A search box filters all groups.
    - "+ Add skill" (name + group).
    - Remove shows an "Undo" toast.
    - The skill vs tool distinction stays in data (display only, no score effect).
11. **Sticky section menu:**
    - entries: Roles · Projects · Education · Skills · Certifications · More proof · Application details — each with a count;
    - jumps to the section and highlights the current one;
    - on mobile it's a dropdown.
12. **"Needs your attention" box** at the top, listing:
    - year-only dates;
    - items dropped by the quote check (with "Add back");
    - conflicts flagged between resume and LinkedIn;
    - sources still being read.
    - Each item links to its card. The box hides when empty.
13. **Sticky bottom bar** with "Confirm profile" (disabled with a reason while sources are still reading or a card is in edit mode).

## 3. Phase C — Dedupe fix (bug fix; changes scoring inputs)
14. **Name normalization** (code, shared by claims, certifications and projects):
    - lowercase; strip punctuation; collapse spaces;
    - strip exam codes in brackets (e.g. "(MLA-C01)", "(SAA-C03)") for matching only, keeping them for display;
    - strip a bracketed acronym when it matches the initials of the name ("Model Context Protocol (MCP)");
    - singularize the last word ("API Gateways" → "API Gateway").
    - Merges use only: exact normalized match, or a **reviewed alias file** (`config/claim_aliases.json`, plus the existing `config/market_synonyms.json`). Seed the alias file with: Hugging Face = HuggingFace; Chroma = ChromaDB; AWS = AWS Services; Docker = Dockerfile; Jupyter Notebook = Jupyter/Google Colab Notebooks.
    - **No fuzzy auto-merges.** Report near-misses as suggestions for Feroz to add to the alias file.
15. **Certifications:**
    - Merge on normalized name, plus the issuer when both have one.
    - Keep the richest fields (credential ID, verify URL, dates, issuer, exam code) and record the list of sources.
    - Show the source chips (e.g. "Resume · LinkedIn").
16. **Projects:** extend the existing repo ↔ resume-project merge so it also matches the normalized name, a slug to title match ("lora-ticket-router" = "LoRA Ticket Router") and the URL. The merged project keeps kind "project" and all sources.
17. **Claims:** one claim per normalized name or alias group. Keep the strongest evidence link (artifact > bullet > claimed_only) and all sources.
18. Record the sources on each merged item for display only. **Don't** feed them into Consistency or any other score in this spec.
19. **Report before/after:**
    - for Feroz's latest profile ("Feroz Khan test 10") and the 16 test profiles: claims and certifications before → after, proof density, Tools, Validation strength and the final score per role;
    - archetype regression: identical, or list the changes and explain them as deliberate;
    - the stuffer/padded gate: any gain for an ID-2 or padded profile must come only from merged duplicates, listed by name.
    - Re-scoring reuses saved judgments. State any new model calls (expected: about 0).

## 4. Phase D — More proof types in Review
20. A **"More proof"** area in Review with one section per type. Each is filled from extraction where found and has "+ Add" with type-specific fields. Empty sections are collapsed into a row of "+ Add …" chips, so people only see what's relevant to them.

| Type | Fields | Maps to existing evidence kind (verify in repo) |
|---|---|---|
| Publications | title, venue, year, link, your role | publication |
| Talks & conferences | title, event, date, link | presentation (Work samples) |
| Awards | name, issuer, date, what it was for | award |
| Hackathons & competitions | event, date, placement, project link | hackathon |
| Open-source contributions | project, link, what you contributed | project (repo if a GitHub link) |
| Case studies | title, link, summary | case study |
| Patents | title, number, date, link | display only [OPEN] |
| Volunteering | organization, role, dates, description | display only |
| Courses | name, provider, date, link | display only |
| Languages | language, proficiency | display only |

21. Display-only types show a small note: "Shown to recruiters, not scored". If a mapped kind doesn't exist in the repo, make that type display-only and report it. Don't add new evidence kinds or scoring rules.
22. Manually added items get `source=user_edit` and are judged at scoring with the existing questions for their kind, the same as extracted items.
23. All types use the Phase B entry card.
24. The "Add your proof" step stays as it is apart from the Phase A fixes. No new upfront forms.

## 5. Tests
- Drop zone: type and size errors, replace, remove, keyboard.
- Input styling tokens applied, including read-only state; placeholders.
- Credential wording, with no "verified" string left on candidate or recruiter screens.
- Punctuation normalization applies to new extractions only.
- Entry card: view/edit/save/cancel, one card in edit at a time, the unsaved-changes prompt, delete + undo, bullet add/delete/reorder (mouse and keyboard), paste-split, the "Current" date toggle.
- Skills grouping, search, add, remove + undo.
- Section menu, attention box, and Confirm disabled with a reason.
- Dedupe:
  - each seeded alias;
  - exam-code and acronym stripping;
  - plurals;
  - slug-to-title matching;
  - the richest fields kept;
  - sources recorded;
  - no fuzzy merges;
  - before/after report produced.
- More proof: each type adds and saves; mapped kinds are created; display-only types never reach scoring.
- Existing Python and browser tests pass; archetype regression as reported in item 19.

## 6. Report
Report everything at the end, one section per phase:
- what was built;
- tests (counts);
- screenshots at 1440×900 and 390 px:
  - drop zone empty and filled;
  - an entry card in view and in edit mode;
  - bullet editing;
  - skills grouped and searched;
  - the section menu;
  - the attention box;
  - certifications after dedupe;
  - More proof with sections open and collapsed;
- the Phase C before/after report;
- model calls (expected: none new);
- decisions marked "my interpretation; please confirm";
- what you couldn't do.
