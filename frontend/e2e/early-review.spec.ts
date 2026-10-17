import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Show Review early (specs/18 phase 3): Review opens once the resume is read; LinkedIn is still being read (the fake
// reader takes a few seconds over this PDF), its items join Review when it finishes, and Confirm waits for it.
// Screenshots go to e2e/.tmp/spec18 (copied to docs/screenshots/spec18 for the report).
const OUT = "e2e/.tmp/spec18";
const CV = path.join(__dirname, ".tmp/cv.pdf");
const LINKEDIN = path.join(__dirname, ".tmp/linkedin-slow.pdf");
fs.mkdirSync(OUT, { recursive: true });

async function earlyReview(page: Page, name: string, prefix: string) {
  await page.goto("/");
  await page.evaluate(() => { try { localStorage.clear(); } catch {} });
  await page.goto("/onboarding");
  await page.getByLabel("Your name *").fill(name);
  await page.getByLabel("Resume *").setInputFiles(CV);
  await page.getByLabel("LinkedIn profile (PDF)").setInputFiles(LINKEDIN);
  await page.getByRole("button", { name: "Read my documents" }).click();

  await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: /Data Scientist/ }).click();
  const go = page.getByRole("button", { name: "Continue to review" });
  await expect(go).toBeEnabled({ timeout: 30_000 });                     // the resume is read; LinkedIn is not
  await expect(page.getByTestId("source-linkedin_pdf")).toContainText("Reading");
  await page.screenshot({ path: `${OUT}/${prefix}1-roles-resume-read.png`, fullPage: true });
  await go.click();

  await expect(page.getByText("Data Analyst — Acme Retail")).toBeVisible({ timeout: 30_000 });
  const still = page.getByTestId("still-reading");
  await expect(still).toContainText("Still reading LinkedIn…");
  const confirm = page.getByRole("button", { name: "Confirm profile" });
  await expect(confirm).toBeDisabled();
  await expect(page.getByTestId("confirm-wait")).toHaveText("You can confirm when everything is read.");
  await expect(page.getByRole("button", { name: "Remove Looker" })).toHaveCount(0);   // not in Skills yet
  await page.screenshot({ path: `${OUT}/${prefix}2-review-linkedin-pending.png`, fullPage: true });

  await expect(still).toHaveCount(0, { timeout: 30_000 });                // LinkedIn finished: its items join
  await expect(page.getByRole("button", { name: "Remove Looker" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Delete Google Data Analytics Certificate" })).toBeVisible();
  await expect(confirm).toBeEnabled();
  await page.screenshot({ path: `${OUT}/${prefix}3-review-all-read.png`, fullPage: true });
}

test("Review opens when the resume is read; LinkedIn joins it; Confirm waits (1440×900)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await earlyReview(page, "Early Reviewer", "desktop-");
});

test("the same at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await earlyReview(page, "Early Reviewer Mobile", "mobile-");
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, "no horizontal scroll").toBeLessThanOrEqual(width);
});
