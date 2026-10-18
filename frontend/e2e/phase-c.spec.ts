import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Candidate experience, phase C (specs/17 §7, §10): the full path (new profile → onboarding → apply → My
// applications → recruiter sends "Not moving forward" → the candidate sees the reasons), every status, the closure
// task, Mark done, the plan's empty state, Role family and the Market template labels. Screenshots: e2e/.tmp/spec17c.
const API = "http://localhost:8010";
const OUT = "e2e/.tmp/spec17c";
const CV = path.join(__dirname, ".tmp/cv.pdf");
const ROOT = path.resolve(__dirname, "../..");
const DS = "baseline-data-scientist-entry";
fs.mkdirSync(OUT, { recursive: true });

async function shot(page: Page, name: string, fullPage = true) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage });
}
async function noSideScroll(page: Page) {
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, `no horizontal scroll on ${page.url()}`).toBeLessThanOrEqual(width);
}
async function wait(api: APIRequestContext, task: string) {
  for (let i = 0; i < 160; i++) {
    const t = await (await api.get(`${API}/tasks/${task}`)).json();
    if (t.status !== "running") return t;
    await new Promise((r) => setTimeout(r, 250));
  }
}
/** Ages an application in the e2e database (the closure task needs one that waited CLOSURE_DAYS). Test only. */
function age(appId: string, days: number) {
  execFileSync(path.join(ROOT, ".venv/bin/python"), ["-c",
    "import sqlite3, sys, datetime as d; c = sqlite3.connect('/tmp/rightstaff-e2e.db'); " +
    "t = (d.datetime.now(d.timezone.utc) - d.timedelta(days=int(sys.argv[2]))).isoformat(timespec='seconds'); " +
    "c.execute('UPDATE applications SET applied_at = ? WHERE id = ?', (t, sys.argv[1])); c.commit()", appId, String(days)]);
}

test("full path, every status, closure, Mark done, plan empty state, Role family, template labels", async ({ page, request }) => {
  test.setTimeout(240_000);
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });

  // 1. New profile → onboarding (UI)
  await page.goto("/");
  await page.evaluate(() => { try { localStorage.clear(); } catch {} });
  await page.goto("/");
  await expect(page).toHaveURL(/\/onboarding$/);
  await page.getByLabel("Your name *").fill("Morgan Avery");
  await page.getByLabel("Resume *").setInputFiles(CV);
  await page.getByRole("button", { name: "Read my documents" }).click();
  await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: "Data Scientist" }).click();
  await expect(page.getByRole("button", { name: "Continue to review" })).toBeEnabled({ timeout: 30_000 });
  await page.getByRole("button", { name: "Continue to review" }).click();
  await page.getByRole("button", { name: "Confirm profile" }).click({ timeout: 30_000 });
  await expect(page.getByTestId(`score-card-${DS}`).getByText("Build an A/B test project")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Skip for now" }).click();
  await page.getByRole("button", { name: "Start applying" }).click();
  const cid = await page.evaluate(() => localStorage.getItem("rightstaff.currentCandidate"));
  expect(cid).toBeTruthy();

  // 2. Mark done on the next best action (changes no score)
  await expect(page.getByText("Next best action")).toBeVisible();
  await page.getByRole("button", { name: "Mark done" }).click();
  await expect(page.getByTestId("done-note")).toContainText("Add the proof to your profile and check again to update your score.");
  await shot(page, "mark-done", false);
  await page.getByTestId("done-note").getByRole("button", { name: "Undo" }).click();
  await expect(page.getByTestId("done-note")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Mark done" })).toBeVisible();

  // 3. Open a job → Apply → status visible in My applications
  await page.getByRole("button", { name: /^Data Scientist I/ }).first().click();
  const pane = page.getByRole("article", { name: "Job: Data Scientist I" });
  await pane.getByRole("button", { name: "Apply" }).click();
  const sheet = page.getByRole("dialog", { name: "What the recruiter will see" });
  await sheet.getByLabel("My profile is up to date").check();
  await sheet.getByRole("button", { name: "Apply" }).click();
  await expect(pane.getByText("Applied", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "My applications" }).click();
  const first = page.getByTestId("application").filter({ hasText: "Data Scientist I" });
  await expect(first.getByTestId("status-chip")).toHaveText("Applied");

  // 4. Recruiter: Not moving forward → template draft → Send (Outbox provider, demo redirect)
  const jobs = await (await request.get(`${API}/candidates/${cid}/applications`)).json();
  const { application_id: appId, job_id: jobId } = jobs[0];
  await page.goto(`/recruiter/jobs/${jobId}?app=${appId}`);
  const panel = page.getByRole("article", { name: /Profile of Morgan Avery/ });
  await panel.getByRole("button", { name: "Not moving forward" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByLabel("Email body")).toHaveValue(/not to move forward/, { timeout: 20_000 });
  await drawer.getByRole("button", { name: "Send", exact: true }).click();
  await expect(panel.getByRole("button", { name: /Not moving forward · undo/ })).toBeVisible();

  // 5. Candidate sees the decision with the reasons, each linked to the plan
  await page.goto("/applications");
  await expect(first.getByTestId("status-chip")).toHaveText("Not moving forward");
  await first.getByRole("button").first().click();
  await expect(first.getByText("What the recruiter told you")).toBeVisible();
  await expect(first.getByRole("link", { name: "See this in your plan" }).first()).toBeVisible();
  await shot(page, "desktop-status-not-moving-forward");

  // 6. Every other status: apply to more jobs (API, same apply logic) and move them on the recruiter board
  const home = await (await request.get(`${API}/candidates/${cid}/jobs`)).json();
  const legacy = ["Northwind Analytics", "Quarry Labs", "Fernway Health"];        // the recruiter test's own jobs
  const others = home.jobs.filter((j: { id: string; applied: unknown; company: string }) =>
    j.id !== jobId && !j.applied && !legacy.includes(j.company)).slice(0, 6);
  const plan = [null, "viewed", "Shortlisted", "Phone screen", "Offer", "Hired"];
  for (let i = 0; i < others.length; i++) {
    await wait(request, (await (await request.post(`${API}/candidates/${cid}/apply`, { data: { job_id: others[i].id } })).json()).task_id);
  }
  const mine = await (await request.get(`${API}/candidates/${cid}/applications`)).json();
  for (let i = 0; i < others.length; i++) {
    const a = mine.find((x: { job_id: string }) => x.job_id === others[i].id);
    const step = plan[i];
    if (step === "viewed") await request.post(`${API}/applications/${a.application_id}/opened`);
    else if (step) {
      const board = await (await request.get(`${API}/jobs/${others[i].id}/pipeline`)).json();
      const st = board.stages.find((s: { name: string }) => s.name === step);
      await request.patch(`${API}/applications/${a.application_id}/stage`, { data: { stage_id: st.id } });
    }
    if (i === 0) age(a.application_id, 20);                       // waited 20 days: closure task
  }
  await page.goto("/applications");
  for (const s of ["Applied", "Viewed by recruiter", "Shortlisted", "Interview", "Offer", "Hired", "Not moving forward"]) {
    await expect(page.getByTestId("status-chip").filter({ hasText: new RegExp(`^${s}`) }).first()).toBeVisible();
  }
  await expect(page.getByText("Waiting for the recruiter. RightStaff reminds recruiters to answer every application.")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/judg|match \d|score \d|rank/i);
  await shot(page, "desktop-my-applications-list");
  for (const [s, name] of [["Viewed by recruiter", "viewed"], ["Shortlisted", "shortlisted"], ["Interview", "interview"],
    ["Offer", "offer"], ["Hired", "hired"], ["Applied", "applied-waiting"]] as const) {
    const item = page.getByTestId("application").filter({ has: page.getByTestId("status-chip").filter({ hasText: new RegExp(`^${s}`) }) }).first();
    await item.getByRole("button").first().click();
    await expect(item.getByRole("list", { name: /Timeline for/ })).toBeVisible();
    await item.screenshot({ path: `${OUT}/desktop-status-${name}.png` });
    await item.getByRole("button").first().click();
  }

  // 7. Closure task on the recruiter's Tasks (a reminder only: nothing sent)
  await page.goto("/recruiter/tasks");
  await expect(page.getByText(/Morgan Avery has waited 20 days for an answer/)).toBeVisible();
  await shot(page, "recruiter-tasks-closure", false);

  // 8. Plan page empty state for "What higher scorers have" (scripted test run here)
  await page.goto(`/scores?role=${DS}&tab=plan`);
  await expect(page.getByTestId("peer-note")).toContainText("What higher scorers have:");
  await shot(page, "plan-empty-state", false);

  // 9. Create job → step 2 Role family (suggested from the title, editable)
  const draft = await (await request.post(`${API}/jobs/drafts`)).json();
  await page.goto(`/recruiter/jobs/${draft.job_id}/edit`);
  await page.getByLabel("Title").fill("Machine Learning Engineer");
  await expect(page.getByLabel("Role family")).toHaveValue("baseline-ai-engineer-entry", { timeout: 10_000 });
  await page.getByLabel("Role family").scrollIntoViewIfNeeded();
  await shot(page, "create-job-role-family", false);
  await page.getByLabel("Role family").selectOption("none");
  await expect(page.getByLabel("Role family")).toHaveValue("none");
  await request.delete(`${API}/jobs/${draft.job_id}/draft`);

  // 10. Market templates in "Copy an existing job": each says what it is based on
  await page.goto("/recruiter/jobs/new");
  const select = page.getByLabel("Job to copy");
  await expect(select.locator("optgroup[label^='Market templates'] option").first()).toHaveText(/Based on \d+ postings? from \d+ compan(y|ies)/);
  await select.evaluate((el) => {
    (el as HTMLSelectElement).size = 12; el.style.height = "auto";
    el.querySelector("optgroup[label^='Market templates']")?.scrollIntoView({ block: "end" });
  });
  await shot(page, "template-labels", false);

  // 11. 390 px: candidate screens without horizontal scroll
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/applications");
  await expect(page.getByTestId("status-chip").first()).toBeVisible();
  await noSideScroll(page);
  await shot(page, "mobile-my-applications-list");
  const nmf = page.getByTestId("application").filter({ hasText: "Not moving forward" }).first();
  await nmf.getByRole("button").first().click();
  await expect(nmf.getByText("What the recruiter told you")).toBeVisible();
  await shot(page, "mobile-status-not-moving-forward");
  for (const url of ["/", "/jobs", `/jobs/${jobId}`, "/scores", `/scores?role=${DS}&tab=plan`, "/profile", "/market"]) {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    await noSideScroll(page);
  }
  expect(problems).toEqual([]);
});
