import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Candidate experience, phase B (specs/17 §4–§6, §9): Home with the ranked jobs list and detail pane, the JobMatch
// preview, job detail evidence, the apply sheet, Not interested and the empty states. Screenshots: e2e/.tmp/spec17b.
const API = process.env.E2E_API ?? "http://localhost:8010";
const OUT = "e2e/.tmp/spec17b";
const CV = path.join(__dirname, ".tmp/cv.pdf");
const DS = "baseline-data-scientist-entry", AI = "baseline-ai-engineer-entry";
fs.mkdirSync(OUT, { recursive: true });

type HomeJobs = { jobs: { id: string; title: string; target: boolean; group: string | null; role_baseline_id: string | null }[] };

async function shot(page: Page, name: string, fullPage = true) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage });
}

async function wait(api: APIRequestContext, task: string) {
  for (let i = 0; i < 120; i++) {
    const t = await (await api.get(`${API}/tasks/${task}`)).json();
    if (t.status !== "running") return t;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("task timed out");
}

/** A profile set up through the API: documents read, roles picked, confirmed and scored. */
async function profile(api: APIRequestContext, name: string, roles: string[], confirm = true) {
  const cid = (await (await api.post(`${API}/candidates`, { data: { name } })).json()).id;
  const read = await (await api.post(`${API}/candidates/${cid}/onboard`, { multipart: { onboarding: "true",
    resume: { name: "cv.pdf", mimeType: "application/pdf", buffer: fs.readFileSync(CV) } } })).json();
  await api.put(`${API}/candidates/${cid}/targets`, { data: { baseline_ids: roles } });
  await wait(api, read.task_id);
  await api.put(`${API}/candidates/${cid}/onboarding`, { data: { step: "review" } });
  if (!confirm) return cid;
  const s = await (await api.post(`${API}/candidates/${cid}/baseline-scores`)).json();
  await wait(api, s.task_id);
  await api.put(`${API}/candidates/${cid}/onboarding`, { data: { step: "done" } });
  return cid;
}

async function viewAs(page: Page, cid: string) {
  await page.goto("/profile");
  await page.evaluate((id) => { try { localStorage.setItem("rightstaff.currentCandidate", id); } catch {} }, cid);
}

async function noSideScroll(page: Page) {
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, "no horizontal scroll").toBeLessThanOrEqual(width);
}

test("Home: ranked jobs, groups, job detail, apply sheet, Not interested, empty states (1440×900 and 390 px)", async ({ page, request }) => {
  test.setTimeout(240_000);
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(e.message));

  // JobMatch is a stable hash of the candidate and the job: find a profile whose jobs fall in all three groups.
  let cid = "";
  for (let i = 0; i < 25 && !cid; i++) {
    const c = await profile(request, `Rowan Ellis ${i + 1}`, [DS, AI]);
    const home: HomeJobs = await (await request.get(`${API}/candidates/${c}/jobs`)).json();
    const groups = new Set(home.jobs.filter((j) => j.target).map((j) => j.group));
    if (groups.has("strong") && groups.has("close") && groups.has("stretch")) cid = c;
  }
  expect(cid, "a profile with Strong, Close and Stretch jobs").not.toBe("");
  const home: HomeJobs = await (await request.get(`${API}/candidates/${cid}/jobs`)).json();

  await page.setViewportSize({ width: 1440, height: 900 });
  await viewAs(page, cid);
  await page.goto("/");
  await expect(page.getByText("Jobs for you")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Jobs", exact: true })).toBeVisible();
  for (const g of ["Strong match", "Close: one step away", "Stretch"]) await expect(page.getByRole("region", { name: g })).toBeVisible();
  await expect(page.getByRole("region", { name: "Other jobs" }).getByTestId("job-row")).toHaveCount(0);   // collapsed
  await expect(page.getByText("Preview").first()).toBeVisible();
  await expect(page.getByText("Salary not listed").first()).toBeVisible();
  await shot(page, "desktop-1-home", false);
  for (const [g, name] of [["Strong match", "strong"], ["Close: one step away", "close"], ["Stretch", "stretch"]]) {
    await page.getByRole("region", { name: g }).screenshot({ path: `${OUT}/group-${name}.png` });
  }
  await page.getByRole("button", { name: /Other jobs/ }).click();
  await expect(page.getByRole("region", { name: "Other jobs" }).getByTestId("job-row").first()).toBeVisible();
  await shot(page, "desktop-2-home-other-jobs-open");

  // Job detail with matched evidence (a Data Scientist job: same-role baseline judgments)
  const dsJob = home.jobs.find((j) => j.role_baseline_id === DS && j.title === "Data Scientist I")!;
  await page.getByRole("button", { name: new RegExp(`^${dsJob.title}`) }).first().click();
  const pane = page.getByRole("article", { name: `Job: ${dsJob.title}` });
  await expect(pane.getByText("Your closest evidence · From your Data Scientist baseline score").first()).toBeVisible();
  await expect(pane.getByText("Checked when JobMatch launches").first()).toBeVisible();          // "Insurance or risk modeling"
  await expect(pane.getByTestId("baseline-under-jobmatch")).toContainText("Your baseline score for Data Scientist:");
  await expect(pane.getByText("What would raise your match")).toBeVisible();
  await shot(page, "desktop-3-detail-with-evidence");

  // Job detail without matched evidence (a Product Manager job: no baseline score for that role)
  const pmJob = home.jobs.find((j) => j.title === "Associate Product Manager, Payments")!;
  await page.getByRole("button", { name: new RegExp(`^${pmJob.title}`) }).first().click();
  const pm = page.getByRole("article", { name: `Job: ${pmJob.title}` });
  await expect(pm.getByText("No baseline score for Product Manager yet.")).toBeVisible();
  await expect(pm.getByText("Your closest evidence")).toHaveCount(0);
  await shot(page, "desktop-4-detail-without-evidence");

  // Apply sheet: "What the recruiter will see"
  await page.getByRole("button", { name: new RegExp(`^${dsJob.title}`) }).first().click();
  await pane.getByRole("button", { name: "Apply" }).click();
  const sheet = page.getByRole("dialog", { name: "What the recruiter will see" });
  await expect(sheet.getByText("They don't see your baseline score or the JobMatch preview.", { exact: false })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Apply" })).toBeDisabled();
  await page.waitForTimeout(400);                                          // let the panel finish opening
  await shot(page, "desktop-5-apply-sheet", false);
  await sheet.getByLabel("My profile is up to date").check();
  await sheet.getByRole("button", { name: "Apply" }).click();
  await expect(pane.getByText("Applied", { exact: true })).toBeVisible({ timeout: 30_000 });

  // Not interested (optional reason) hides the job
  const hideMe = home.jobs.find((j) => j.title === "Junior Data Scientist, Growth")!;
  await page.getByRole("button", { name: `Not interested in ${hideMe.title}` }).click();
  await page.getByRole("dialog").getByLabel("Salary", { exact: true }).check();
  await page.waitForTimeout(400);                                          // let the panel finish opening
  await shot(page, "desktop-6-not-interested", false);
  await page.getByRole("button", { name: "Hide job" }).click();
  await expect(page.getByRole("button", { name: new RegExp(`^${hideMe.title}`) })).toHaveCount(0);
  await expect(page.getByText("1 hidden job")).toBeVisible();

  // Empty states: filters, no jobs for your roles, everything hidden
  await page.getByRole("textbox", { name: "Location" }).fill("Atlantis");
  await expect(page.getByText("No jobs match these filters")).toBeVisible();
  await shot(page, "empty-1-filters", false);
  await page.getByRole("button", { name: "Clear filters" }).click();
  for (const j of home.jobs.filter((x) => x.target)) await request.post(`${API}/candidates/${cid}/jobs/${j.id}/hide`, { data: {} });
  await page.reload();
  await expect(page.getByText("No jobs for your roles yet")).toBeVisible();
  await shot(page, "empty-2-no-jobs-for-your-roles", false);
  for (const j of home.jobs) await request.post(`${API}/candidates/${cid}/jobs/${j.id}/hide`, { data: {} });
  await page.reload();
  await expect(page.getByText("You've hidden every open job")).toBeVisible();
  await shot(page, "empty-3-all-hidden", false);
  await page.getByRole("button", { name: "Show hidden jobs" }).click();
  await page.waitForTimeout(400);                                          // let the panel finish opening
  await shot(page, "empty-4-hidden-list", false);
  for (const j of home.jobs) await request.post(`${API}/candidates/${cid}/jobs/${j.id}/unhide`);
  await page.keyboard.press("Escape");

  // Profile not confirmed: matches and applying on hold
  const pending = await profile(request, "Jordan Mensah", [DS], false);
  await viewAs(page, pending);
  await page.goto("/jobs");
  await expect(page.getByText("Your profile isn't confirmed yet", { exact: false })).toBeVisible();
  await shot(page, "empty-5-profile-not-confirmed", false);

  // 390 px: the list, then a job as its own page
  await viewAs(page, cid);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("Jobs for you")).toBeVisible();
  await noSideScroll(page);
  await shot(page, "mobile-1-home");
  await page.getByRole("button", { name: new RegExp(`^${dsJob.title}`) }).first().click();
  await expect(page).toHaveURL(new RegExp(`/jobs/${dsJob.id}$`));
  await expect(page.getByText("Your closest evidence · From your Data Scientist baseline score").first()).toBeVisible();
  await noSideScroll(page);
  await shot(page, "mobile-2-job-detail");
  expect(problems).toEqual([]);
});
