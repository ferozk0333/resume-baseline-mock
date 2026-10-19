import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

// Onboarding wait screens (specs/17): reading and scoring progress, driven here by scripted progress answers so
// every state can be checked; "Good to know" tips with a controlled clock; one primary-role control.
const API = process.env.E2E_API ?? "http://localhost:8010";
const OUT = "e2e/.tmp/spec17-wait";
const CV = path.join(__dirname, ".tmp/cv.pdf");
const DS = "baseline-data-scientist-entry", AI = "baseline-ai-engineer-entry";
fs.mkdirSync(OUT, { recursive: true });

async function wait(api: APIRequestContext, task: string) {
  for (let i = 0; i < 120; i++) {
    const t = await (await api.get(`${API}/tasks/${task}`)).json();
    if (t.status !== "running") return t;
    await new Promise((r) => setTimeout(r, 250));
  }
}

/** A profile whose documents are read (step 2). With roles, it is also confirmed and scored (step 4). */
async function profile(api: APIRequestContext, name: string, roles: string[] = []) {
  const cid = (await (await api.post(`${API}/candidates`, { data: { name } })).json()).id;
  const read = await (await api.post(`${API}/candidates/${cid}/onboard`, { multipart: { onboarding: "true",
    resume: { name: "cv.pdf", mimeType: "application/pdf", buffer: fs.readFileSync(CV) } } })).json();
  await wait(api, read.task_id);
  if (roles.length) {
    await api.put(`${API}/candidates/${cid}/targets`, { data: { baseline_ids: roles } });
    await api.put(`${API}/candidates/${cid}/onboarding`, { data: { step: "review" } });
    await wait(api, (await (await api.post(`${API}/candidates/${cid}/baseline-scores`)).json()).task_id);
  }
  return cid;
}

async function viewAs(page: Page, cid: string) {
  await page.goto("/profile");
  await page.evaluate((id) => { try { localStorage.setItem("rightstaff.currentCandidate", id); } catch {} }, cid);
}

/** Answer a polled endpoint with the current scripted body; the test moves on with `next()`. */
function script(bodies: unknown[]) {
  let i = 0;
  const handler = (route: Route) => route.fulfill({ json: bodies[Math.min(i, bodies.length - 1)] });
  return Object.assign(handler, { next: () => { i += 1; } });
}

async function noSideScroll(page: Page) {
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll).toBeLessThanOrEqual(width);
}

const src = (type: string, label: string, status: string, extra = {}) =>
  ({ type, label, status, error: null, counts: null, ...extra });
const READING = [
  { sources: [src("resume", "Resume", "reading"), src("linkedin_pdf", "LinkedIn", "waiting"), src("github", "GitHub", "waiting")],
    stage: "read", stage_label: "Reading your resume", percent: 10, done: false, error: null, typical_minutes: 2 },
  { sources: [src("resume", "Resume", "done", { counts: { roles: 4, projects: 6, skills: 21 } }),
    src("linkedin_pdf", "LinkedIn", "failed", { error: "This PDF has no text layer (it looks like a scan). Upload a text PDF or a DOCX." }),
    src("github", "GitHub", "reading")],
    stage: "find", stage_label: "Finding your roles, projects and skills", percent: 45, done: false, error: null, typical_minutes: 2 },
  { sources: [src("resume", "Resume", "done", { counts: { roles: 4, projects: 6, skills: 21 } }),
    src("linkedin_pdf", "LinkedIn", "failed", { error: "This PDF has no text layer (it looks like a scan). Upload a text PDF or a DOCX." }),
    src("github", "GitHub", "done", { counts: { roles: 0, projects: 5, skills: 9 } })],
    stage: "done", stage_label: "Your documents are read", percent: 100, done: true, error: null, typical_minutes: null },
];
const dims = (e: number, x: number, a: number, t: number) => ({ experience: e, expertise: x, authority: a, trust: t });
const role = (id: string, title: string, primary: boolean, status: string, d = dims(0, 0, 0, 0)) =>
  ({ baseline_id: id, title, is_primary: primary, status, dimensions: d });
const SCORING = [
  { roles: [role(DS, "Data Scientist", true, "scoring", dims(0.4, 0.2, 0, 0)), role(AI, "AI Engineer", false, "waiting")],
    overall: 0.075, line: "Scoring your profile for 2 roles", all_ready: false, running: true, typical_minutes: 3 },
  { roles: [role(DS, "Data Scientist", true, "ready", dims(1, 1, 1, 1)), role(AI, "AI Engineer", false, "scoring", dims(0.6, 0, 0, 0))],
    overall: 0.575, line: "Scoring your profile for 2 roles · Data Scientist ready", all_ready: false, running: true, typical_minutes: 3 },
  { roles: [role(DS, "Data Scientist", true, "ready", dims(1, 1, 1, 1)), role(AI, "AI Engineer", false, "failed", dims(0.6, 0, 0, 0))],
    overall: 0.575, line: "Scoring your profile for 2 roles · Data Scientist ready", all_ready: false, running: false, typical_minutes: null },
];
const AFTER_RETRY = [
  { roles: [role(DS, "Data Scientist", true, "ready", dims(1, 1, 1, 1)), role(AI, "AI Engineer", false, "scoring", dims(0.6, 0.5, 0, 0))],
    overall: 0.64, line: "Scoring your profile for 2 roles · Data Scientist ready", all_ready: false, running: true, typical_minutes: 3 },
  { roles: [role(DS, "Data Scientist", true, "ready", dims(1, 1, 1, 1)), role(AI, "AI Engineer", false, "ready", dims(1, 1, 1, 1))],
    overall: 1, line: "Your scores are ready", all_ready: true, running: false, typical_minutes: null },
];

for (const [w, h, tag] of [[1440, 900, "desktop"], [390, 844, "mobile"]] as const) {
  test(`reading wait and one primary control (${tag})`, async ({ page, request }) => {
    await page.setViewportSize({ width: w, height: h });
    const cid = await profile(request, `Kai Morgan ${tag}`);
    await viewAs(page, cid);
    const reading = script(READING);
    await page.route("**/reading-progress*", reading);
    await page.goto("/onboarding");
    const panel = page.getByRole("region", { name: "Reading your documents" });
    await expect(panel.getByTestId("source-resume")).toContainText("Reading");
    await expect(panel.getByTestId("source-linkedin_pdf")).toContainText("Waiting");
    await expect(panel.getByTestId("wait-line")).toHaveText("Reading your resume");
    await expect(panel.getByText("Usually takes about 2 minutes.")).toBeVisible();
    await page.screenshot({ path: `${OUT}/${tag}-reading-1.png`, fullPage: true });
    reading.next();
    await expect(panel.getByTestId("counts-resume")).toHaveText("4 roles · 6 projects · 21 skills");
    await expect(panel.getByTestId("source-linkedin_pdf")).toContainText("Couldn't read");
    await expect(panel.getByText("This PDF has no text layer", { exact: false })).toBeVisible();
    await page.screenshot({ path: `${OUT}/${tag}-reading-2.png`, fullPage: true });
    reading.next();
    await expect(panel.getByTestId("wait-line")).toHaveText("Your documents are read");
    await expect(panel.getByTestId("counts-github")).toHaveText("0 roles · 5 projects · 9 skills");
    await expect(page.getByText("Usually takes about")).toHaveCount(0);
    if (tag === "mobile") await noSideScroll(page);

    // One primary control: the pill on the primary card, "Make primary" on the others; no radio row
    await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: "Data Scientist" }).click();
    await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: "AI Engineer" }).click();
    await expect(page.getByTestId(`role-card-${DS}`)).toContainText("Primary");
    await expect(page.getByRole("radio")).toHaveCount(0);
    await page.getByRole("button", { name: "Make AI Engineer primary" }).click();
    await expect(page.getByTestId(`role-card-${AI}`)).toContainText("Primary");
    await expect(page.getByTestId(`role-card-${DS}`)).toContainText("Make primary");
    await page.screenshot({ path: `${OUT}/${tag}-reading-3-done-primary.png`, fullPage: true });
    await expect.poll(async () => {                                // the page saves in the background
      const saved = await (await request.get(`${API}/candidates/${cid}/onboarding`)).json();
      return saved.targets.find((t: { is_primary: boolean }) => t.is_primary)?.baseline_id;
    }).toBe(AI);
  });

  test(`scoring wait: rows, segments, Ready, error + Retry, auto-advance (${tag})`, async ({ page, request }) => {
    await page.setViewportSize({ width: w, height: h });
    const cid = await profile(request, `Lee Park ${tag}`, [DS, AI]);
    await viewAs(page, cid);
    let retried = false;
    const first = script(SCORING), second = script(AFTER_RETRY);
    await page.route("**/scoring-progress*", (r) => (retried ? second(r) : first(r)));
    await page.route("**/baseline-scores", (r) => {
      if (r.request().method() !== "POST") return r.fallback();
      retried = true;
      return r.fulfill({ status: 202, json: { task_id: "retry" } });
    });
    await page.route(/\/candidates\/[^/]+\/onboarding$/, async (r) => {                       // show the wait, not the finished results
      if (r.request().method() !== "GET") return r.fallback();
      const json = await (await r.fetch()).json();
      json.score_task = { ...json.score_task, status: "running" };
      return r.fulfill({ json });
    });
    await page.goto("/onboarding");
    const view = page.getByRole("region", { name: "Scoring your profile" });
    await expect(view.getByTestId(`role-${DS}`)).toContainText("Scoring");
    await expect(view.getByTestId(`role-${AI}`)).toContainText("Waiting");
    await expect(view.getByTestId(`fill-${DS}-experience`)).toHaveAttribute("style", /width: 40%/);
    await expect(view.getByTestId(`fill-${DS}-authority`)).toHaveAttribute("style", /width: 0%/);
    await expect(page.getByTestId("tips-scoring")).toBeVisible();
    await page.screenshot({ path: `${OUT}/${tag}-scoring-1.png`, fullPage: true });
    first.next();
    await expect(view.getByTestId(`role-${DS}`)).toContainText("Ready");
    first.next();
    await expect(view.getByTestId("wait-line")).toHaveText("Scoring your profile for 2 roles · Data Scientist ready");
    await expect(view.getByTestId(`role-${DS}`)).toContainText("Ready");
    await expect(view.getByText("Couldn't finish this role. We'll try again.")).toBeVisible();
    expect(await view.textContent()).not.toMatch(/of 19|judg|weight/i);                    // no internal counts
    await page.screenshot({ path: `${OUT}/${tag}-scoring-2-error.png`, fullPage: true });
    await view.getByRole("button", { name: "Retry" }).click();
    await expect(view.getByTestId(`role-${AI}`)).toContainText("Scoring");
    second.next();
    await expect(view.getByRole("button", { name: "See my scores" })).toBeVisible();        // shown for 1.5 s
    await expect(view.getByTestId("wait-line")).toHaveText("Your scores are ready");
    await page.screenshot({ path: `${OUT}/${tag}-scoring-3-ready.png` });
    // Auto-advance after 1.5 s to the results
    await expect(page.getByTestId(`score-card-${DS}`)).toBeVisible({ timeout: 5_000 });
    if (tag === "mobile") await noSideScroll(page);
  });
}

test("Good to know: rotates every 8 s, pauses on hover, stops after an arrow, never moves with reduced motion", async ({ page, request }) => {
  const cid = await profile(request, "Ari Quinn");
  await viewAs(page, cid);
  await page.route("**/reading-progress*", script([READING[0]]));
  await page.clock.install();
  await page.goto("/onboarding");
  const tips = page.getByTestId("tips-reading");
  const title = tips.getByTestId("tip-title");
  await expect(title).toHaveText("Show it once");
  await page.clock.fastForward(8_100);
  await expect(title).toHaveText("Only what you wrote");
  await tips.hover();                                                     // paused while hovered
  await page.clock.fastForward(20_000);
  await expect(title).toHaveText("Only what you wrote");
  await page.mouse.move(0, 0);
  await page.clock.fastForward(8_100);
  await expect(title).toHaveText("More sources, more credibility");
  await tips.getByRole("button", { name: "Previous tip" }).click();       // the visitor took over: no more auto-advance
  await expect(title).toHaveText("Only what you wrote");
  await page.mouse.move(0, 0);
  await page.locator("body").focus();
  await page.clock.fastForward(30_000);
  await expect(title).toHaveText("Only what you wrote");
  await tips.getByRole("button", { name: "Tip 4: You stay in control" }).click();
  await expect(title).toHaveText("You stay in control");
  await tips.getByRole("button", { name: "Next tip" }).press("ArrowRight");  // keyboard
  await expect(title).toHaveText("Show it once");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  await expect(title).toHaveText("Show it once");
  await page.clock.fastForward(30_000);
  await expect(title).toHaveText("Show it once");
});
