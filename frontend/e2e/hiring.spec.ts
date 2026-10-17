import { expect, test } from "@playwright/test";

// specs/16 §7: shortlist → approve invite → paste "Tuesday 11:00 works" → confirm slot → feedback → advance.
// API on fakes: template drafts and code-rule reply reading (no model calls); outbox only.
test("hiring workflow: invite → reply → slot → feedback → advance", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/recruiter");
  await page.getByRole("link", { name: "Associate Product Manager", exact: true }).click();
  const list = page.getByRole("list", { name: "Applicants" });
  await expect(list.getByRole("listitem").first()).toBeVisible({ timeout: 20_000 });
  const first = list.getByRole("listitem").first();
  const name = (await first.getByRole("button").first().getAttribute("aria-label"))!.split(":")[0];

  // §0: start from a candidate who is not shortlisted (the one open in the panel), confirm the move
  await page.getByRole("button", { name: /^Start interview loop/ }).click();
  await expect(page.getByRole("dialog")).toContainText("Move to Shortlisted and start?");
  await page.getByRole("button", { name: "Move and start" }).click();
  await expect(page).toHaveURL(/\/workflow$/);
  const step = page.getByRole("region", { name: "Current step" });
  await expect(step.getByRole("heading", { name: "Approve scheduling email" })).toBeVisible({ timeout: 20_000 });
  await expect(step.getByText("Template draft")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-1-approve-invite.png" });

  // Tasks page lists it
  await page.getByRole("link", { name: /^Tasks/ }).click();
  await expect(page.getByRole("list", { name: "Tasks" })).toContainText(`Approve scheduling email – ${name}`);
  await page.screenshot({ path: "e2e/.tmp/wf-tasks.png" });
  await page.getByRole("link", { name: new RegExp(`Approve scheduling email – ${name}`) }).click();

  await step.getByRole("button", { name: "Approve and send" }).click();
  await expect(step.getByRole("heading", { name: "Waiting for the candidate's reply" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Thread" })).toContainText("[RS-");
  await page.screenshot({ path: "e2e/.tmp/wf-2-await-reply.png" });

  await step.getByLabel("Simulate / paste the candidate's reply").fill("Tuesday 11:00 works for me. Thanks!");
  await step.getByRole("button", { name: "Add reply" }).click();
  await expect(step.getByRole("heading", { name: "Reply received – pick a slot" })).toBeVisible();
  await expect(step.getByText("Slot chosen")).toBeVisible();
  await expect(step.getByText("candidate's time")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-3-confirm-slot.png" });
  await step.getByRole("button", { name: /Confirm slot/ }).click();

  await expect(step.getByRole("heading", { name: "Approve confirmation with calendar invite" })).toBeVisible();
  await expect(step.getByText(/invite\.ics/)).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-4-approve-confirmation.png" });
  await step.getByRole("button", { name: "Approve and send" }).click();
  await expect(step.getByText(/Phone screen on Tue/)).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-5-scheduled.png" });

  // Board shows the status badge
  await page.getByRole("link", { name: "Back to the job" }).click();
  await page.getByRole("button", { name: "Pipeline" }).click();
  await expect(page.getByRole("region", { name: "Phone screen stage" })).toContainText(/Scheduled Tue/);
  await page.screenshot({ path: "e2e/.tmp/wf-board.png" });
  await page.getByRole("region", { name: "Phone screen stage" }).getByRole("link", { name }).click();

  await step.getByRole("button", { name: "Interview done: enter feedback" }).click();
  await expect(step.getByRole("heading", { name: "Feedback due" })).toBeVisible();
  const card = page.getByRole("form", { name: "Scorecard" });
  await card.getByLabel("Interviewer").fill("Priya Shah");
  const ratings = card.getByRole("combobox", { name: / rating$/ });
  for (let i = 0; i < (await ratings.count()); i++) await ratings.nth(i).selectOption("3");
  await card.getByRole("textbox", { name: / notes$/ }).first().fill("Clear product sense; walked through a launch.");
  await card.getByLabel("Overall notes").fill("Probe prioritization in the next round.");
  await card.getByRole("button", { name: "Add scorecard" }).click();
  await expect(step.getByText("Priya Shah")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-6-feedback.png" });
  await step.getByRole("button", { name: /Summarize feedback/ }).click();

  await expect(step.getByRole("heading", { name: "Decide the next step" })).toBeVisible();
  await expect(step.getByText("The summary makes no recommendation.")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-7-decide.png" });
  await step.getByRole("button", { name: /^Advance to Interview/ }).click();
  await expect(step.getByRole("heading", { name: "Approve scheduling email" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Stage timeline" }).locator("[aria-current=step]")).toContainText("Scheduling");
  await page.screenshot({ path: "e2e/.tmp/wf-8-next-round.png" });

  // Outbox: the confirmation went with its .ics invite
  await page.getByRole("link", { name: "Outbox" }).click();
  await expect(page.getByText(/Confirmed: Phone screen/).first()).toBeVisible();
  expect(problems).toEqual([]);
});

// Report screenshots: Tasks with several kinds of steps, the board with status badges, the .ics email in the Outbox.
test("tasks and board across several workflows", async ({ page, request }) => {
  const API = "http://localhost:8010";
  await page.setViewportSize({ width: 1440, height: 900 });
  const jobs = await (await request.get(`${API}/jobs`)).json();
  const job = jobs.find((j: { title: string }) => j.title === "AI Engineer");
  const rows = (await (await request.get(`${API}/jobs/${job.id}/applicants`)).json()).applicants;
  const ids: string[] = rows.slice(0, 6).map((r: { application_id: string }) => r.application_id);
  await request.post(`${API}/jobs/${job.id}/workflows`, { data: { application_ids: ids, confirm_move: true } });
  const view = async (id: string) => (await request.get(`${API}/applications/${id}/workflow`)).json();
  const act = (id: string, data: object) => request.post(`${API}/applications/${id}/workflow/action`, { data });
  const reply = (id: string, text: string) => request.post(`${API}/applications/${id}/workflow/reply`, { data: { text } });
  for (const id of ids.slice(0, 5)) await act(id, { action: "send", email_id: (await view(id)).step.email_id });
  await reply(ids[0], "Tuesday 11:00 works for me.");                                 // pick a slot
  await reply(ids[1], "I've accepted another offer, so I'll withdraw.");              // confirm withdrawal
  await reply(ids[2], "Tuesday 11:00 works for me.");                                 // → scheduled → feedback due
  await act(ids[2], { action: "confirm", start: (await view(ids[2])).step.slots[1].start });
  await act(ids[2], { action: "send", email_id: (await view(ids[2])).step.email_id });
  await act(ids[2], { action: "done" });
  await reply(ids[3], "Tuesday 11:00 works for me.");                                 // scheduled
  await act(ids[3], { action: "confirm", start: (await view(ids[3])).step.slots[2].start });
  await act(ids[3], { action: "send", email_id: (await view(ids[3])).step.email_id });
  // ids[4] waits for the candidate; ids[5] still needs its invite approved

  await page.goto("/recruiter/tasks");
  await expect(page.getByRole("list", { name: "Tasks" }).getByRole("listitem")).toHaveCount(4);
  await page.screenshot({ path: "e2e/.tmp/wf-tasks-many.png" });
  await page.goto(`/recruiter/jobs/${job.id}?view=pipeline`);
  await expect(page.getByText("Feedback due")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-board-many.png" });
  await page.goto(`/recruiter/applications/${ids[1]}/workflow`);
  await expect(page.getByRole("heading", { name: "Reply received – confirm withdrawal" })).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/wf-withdrawal.png" });
  await page.goto("/recruiter/outbox");
  const conf = page.getByRole("listitem").filter({ hasText: "📎 invite.ics" }).first();
  await conf.getByText(/Confirmed:/).click();
  await page.screenshot({ path: "e2e/.tmp/wf-outbox-ics.png" });
});
