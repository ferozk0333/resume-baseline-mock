import fs from "node:fs";
import path from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";

// Cleanup round: Tasks (no inbox sentence, no address; one "Check inbox now" with the last check in its tooltip) and the
// recruiter LinkedIn tab (the uploaded PDF in a viewer, or "No LinkedIn PDF uploaded"). Screenshots: e2e/.tmp/cleanup.
const OUT = "e2e/.tmp/cleanup";
const API = "http://localhost:8010";
const CV = path.join(__dirname, ".tmp/cv.pdf");
const LINKEDIN = path.join(__dirname, ".tmp/linkedin-slow.pdf");
fs.mkdirSync(OUT, { recursive: true });

async function reveal(list: Locator, row: Locator) {
  for (let y = 0; y <= 20_000 && (await row.count()) === 0; y += 300) {
    await list.evaluate((el, top) => { el.scrollTop = top; el.dispatchEvent(new Event("scroll")); }, y);
  }
  return row.first();
}

test("Tasks: a single Check inbox now button; no inbox sentence and no email address", async ({ page }) => {
  // The demo API does not poll a mailbox; stand in for one that does, so the button shows.
  await page.route("**/workflows/inbox", (r) => r.fulfill({ json: { enabled: true,
    last_check: { at: "2026-10-17T17:42:00Z", failed: false } } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/recruiter/tasks");
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  const check = page.getByRole("button", { name: "Check inbox now" });
  await expect(check).toBeVisible();
  await expect(page.getByText(/Replies are read|every \d+ min|Everything waiting for you/)).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(/@/);
  await check.hover();
  await expect(page.getByText(/^Last checked at /)).toBeVisible();
  await page.screenshot({ path: `${OUT}/tasks.png` });
});

async function openPanel(page: Page, jobTitle: string, name: string) {
  await page.goto("/recruiter");
  await page.getByRole("link", { name: jobTitle, exact: true }).click();
  const list = page.getByRole("list", { name: "Applicants" });
  await expect(list.getByRole("listitem").first()).toBeVisible({ timeout: 20_000 });
  await (await reveal(list, list.getByRole("button", { name: new RegExp(`^${name}`) }))).click();
  const panel = page.getByRole("article", { name: new RegExp(`Profile of ${name}`) });
  await panel.getByRole("tab", { name: "LinkedIn" }).click();
  return panel;
}

test("recruiter LinkedIn tab: the uploaded PDF, or No LinkedIn PDF uploaded", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  // A candidate with a LinkedIn PDF: onboarding, confirm, then apply to "Data Scientist I" (a job no other test ranks).
  await page.goto("/");
  await page.evaluate(() => { try { localStorage.clear(); } catch {} });
  await page.goto("/onboarding");
  await page.getByLabel("Your name *").fill("Lin Kedin");
  await page.getByLabel("Resume *").setInputFiles(CV);
  await page.getByLabel("LinkedIn profile (PDF)").setInputFiles(LINKEDIN);
  await page.getByRole("button", { name: "Read my documents" }).click();
  await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: /Data Scientist/ }).click();
  await page.getByRole("button", { name: "Continue to review" }).click({ timeout: 30_000 });
  const confirm = page.getByRole("button", { name: "Confirm profile" });
  await expect(confirm).toBeEnabled({ timeout: 30_000 });
  await confirm.click();
  await expect(page.getByRole("button", { name: "Continue" })).toBeVisible({ timeout: 60_000 });
  const cid = await page.evaluate(() => localStorage.getItem("rightstaff.currentCandidate"));
  const job = (await (await request.get(`${API}/open-jobs`)).json()).find((j: { title: string }) => j.title === "Data Scientist I");
  const { task_id } = await (await request.post(`${API}/candidates/${cid}/apply`, { data: { job_id: job.id } })).json();
  for (let i = 0; i < 60; i++) {
    const t = await (await request.get(`${API}/tasks/${task_id}`)).json();
    if (t.status !== "running" && t.status !== "queued") break;
    await page.waitForTimeout(500);
  }

  const withPdf = await openPanel(page, "Data Scientist I", "Lin Kedin");
  const viewer = withPdf.locator("iframe[title='LinkedIn PDF']");
  await expect(viewer).toBeVisible();
  await expect(viewer).toHaveAttribute("src", new RegExp(`/candidates/${cid}/files/linkedin$`));
  const res = await request.get(`${API}/candidates/${cid}/files/linkedin`);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toContain("application/pdf");
  await expect(withPdf.getByText(/^Skills:|From the uploaded LinkedIn PDF/)).toHaveCount(0);   // no parsed list
  await page.waitForTimeout(800);                                                         // the PDF viewer paints
  await page.screenshot({ path: `${OUT}/linkedin-tab-pdf.png` });

  // A demo profile whose LinkedIn was read from text (no PDF file)
  const without = await openPanel(page, "Data Scientist", "Sofia Lindqvist");
  await expect(without.getByTestId("no-linkedin-pdf")).toHaveText("No LinkedIn PDF uploaded");
  await expect(without.locator("iframe")).toHaveCount(0);
  await expect(without.getByRole("tab", { name: "LinkedIn" })).toHaveAttribute("aria-selected", "true");
  await page.waitForTimeout(600);                                                         // tab indicator settles
  await page.screenshot({ path: `${OUT}/linkedin-tab-no-pdf.png` });
});
