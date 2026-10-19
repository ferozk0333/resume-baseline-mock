# 16 — Hiring workflow (human-in-the-loop agent)

Replaces the one-off "Start interview loop" screen from spec 13 with a **workflow per application** that carries a candidate from shortlist to decision. The agent drafts, reads replies and suggests next steps; **a recruiter approves every email and every decision. Nothing is sent automatically.**

## 0. Bugs to fix first
1. "Start interview loop" works only after moving a candidate to Shortlisted **and** ticking their checkbox. It should work from any candidate (single or bulk); if the candidate isn't Shortlisted, confirm "Move to Shortlisted and start?".
2. After sending from the review screen, there is no way back to it. Replaced by the workflow page below, reachable at any time.

## 1. Where a recruiter finds it
- **Workflow page per application** (`/recruiter/applications/{id}/workflow`), opened from:
  - the Pipeline board: clicking a card;
  - the candidate panel: a "Workflow" button in the header and the Activity tab;
  - the new **Tasks** page.
- **Tasks** page (top navigation, with a count badge): everything waiting for the recruiter across jobs, oldest first. Examples: "Approve scheduling email – Rachel Adeyemi", "Reply received – pick a slot", "Feedback due – Noah Kim", "No reply for 3 days – send a nudge?". Each item opens the workflow at that step.
- **Pipeline board cards** show the workflow status: *Needs your action*, *Waiting for candidate*, *Scheduled Oct 20, 10:00*, *Feedback due*.

## 2. Workflow page layout
- **Header:** candidate, job, current stage, match score, buttons (Pause, Not moving forward).
- **Left: stage timeline** (vertical stepper) with each stage's status and date: Shortlisted → Scheduling → Scheduled → Interview → Feedback → Decision → Offer → Hired (or Not moving forward). Stages come from the job's interview plan; each interview stage repeats Scheduling → Scheduled → Interview → Feedback.
- **Centre: current step card**, the one thing that needs attention, with the agent's draft or suggestion and the action buttons.
- **Right: thread and history:** every email sent and received (full text), feedback, decisions, who did what and when.

## 3. The agent graph (LangGraph, SQLite checkpointer, one thread per application)
Every **⏸** is an interrupt that waits for the recruiter.

```
start → draft_invite → ⏸ approve_email → send → await_reply
await_reply ──(reply arrives)──→ read_reply → route:
   • picks a slot / proposes times → propose_slots → ⏸ confirm_slot → send_confirmation (.ics) → scheduled
   • asks a question             → draft_answer → ⏸ approve_email → send → await_reply
   • declines / withdraws        → ⏸ confirm_withdrawal → close
   • unclear                     → ⏸ recruiter reads and chooses the route
await_reply ──(no reply after NUDGE_DAYS)──→ draft_nudge → ⏸ approve_email → send → await_reply
scheduled ──(interview time passes)──→ request_feedback → ⏸ enter_feedback (one or more interviewers)
   → summarize_feedback → ⏸ decide: advance | not moving forward | hold
   advance → next stage (back to draft_invite for the next interview, or draft_offer_note → ⏸ approve_email)
   not moving forward → spec 13 rejection drafting (its own ⏸) → close
```

- **Drafting** (`EMAIL_MODEL`): same rules as spec 13. Grounded in the candidate's evidence and the selected reasons, word limits, no scores, rankings, other candidates or personal characteristics; template fallback.
- **Reading replies** (`REPLY_MODEL`): classify the reply as `slot_chosen` / `times_proposed` / `question` / `decline` / `unclear`, and extract any times with timezone. Quotes required; the classification is shown to the recruiter, never acted on alone.
- **Slots:**
  - Code proposes 2–3 slots from the job's availability windows and interview duration, skipping slots already booked for this job.
  - The recruiter confirms or edits them.
  - Confirmation emails attach an **.ics calendar invite** (no calendar API needed).
  - A Google Calendar connection is out of scope; keep a `CalendarProvider` interface with an `ics` implementation.
- **Feedback:**
  - A short scorecard per interviewer: the stage's focus areas, each rated 1–4 with notes, plus an overall recommendation (strong yes / yes / no / strong no).
  - The model summarizes several scorecards into agreements, disagreements and open questions, citing scorecard text.
  - The **decision is always the recruiter's**; the summary makes no recommendation.
- **Durability:**
  - The workflow survives restarts (checkpointer).
  - Every send is idempotent (one send per approved draft ID), so a refresh or double click can't send twice.

## 4. Receiving candidate replies
Two ways, both feeding `read_reply`:
1. **Simulate / paste reply** on the workflow page (always available). For the demo, the recruiter pastes the candidate's reply or picks a canned one ("Tuesday 11:00 works", "Can we do next week?", "I've accepted another offer").
2. **Inbox polling (optional, `REPLY_INBOX=imap`):**
   - Poll the demo Gmail inbox over IMAP (same app password) every few minutes.
   - Match replies to their workflow with a token in the subject (`[RS-1a2b]`) and the In-Reply-To header.
   - A matched reply creates a Tasks item; the agent reads it, but still waits for the recruiter.
   - Because of the demo redirect, all mail goes to and comes from the demo inbox (DEMO_EMAIL_TO); matching uses the token, not the address.

## 5. Stage changes from the board
- Dragging a card on the Pipeline board moves the stage **and** updates the workflow (e.g. dragging to Interview marks Scheduling as skipped with a note).
- Moving a card never sends anything; if an email would normally follow, a Tasks item "Send stage email?" is created.

## 6. Data
- `workflows`: application_id, LangGraph thread_id, current stage, current step, status (`needs_action` / `waiting_candidate` / `scheduled` / `paused` / `closed`), next due date.
- `workflow_events`: type, payload, actor (`recruiter` / `agent` / `candidate`), created_at.
- `messages`: direction (out / in), subject, body, token, Message-ID, In-Reply-To, intended and actual recipient, draft_id, status, sent_at.
- `interviews`: stage, start, end, timezone, ics_uid, status.
- `scorecards`: interviewer name, focus-area ratings, notes, recommendation.
- Settings: `NUDGE_DAYS` (3), `REPLY_INBOX` (`none` / `imap`), IMAP settings, the existing `DEMO_EMAIL_TO` redirect (still enforced).

## 7. Tests
- Graph paths with the fake model: slot chosen, times proposed, question, decline, unclear, no reply → nudge, feedback → advance, feedback → reject.
- No email leaves without an approve step (assert across all paths); idempotent send.
- Restart in the middle of a workflow resumes at the same step.
- Slot proposal respects availability and existing bookings; .ics content is valid.
- IMAP matching by token with recorded messages (no network).
- Board drag updates the workflow and sends nothing.
- Playwright: shortlist → approve invite → paste reply "Tuesday 11:00 works" → confirm slot → enter feedback → advance.

## 8. Report
- Screenshots: the Tasks page, the workflow page at each step type, the board with status badges, an email with the .ics attachment.
- Model calls per role.
- Whether IMAP polling worked with the demo inbox.
