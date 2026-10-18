import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Candidate experience, phase A (specs/17): first run → onboarding (5 steps) → Home, My scores, Profile.
// Screenshots go to e2e/.tmp/spec17 (copied to docs/screenshots/spec17 for the report).
const OUT = "e2e/.tmp/spec17";
const CV = path.join(__dirname, ".tmp/cv.pdf");
fs.mkdirSync(OUT, { recursive: true });

async function shot(page: Page, name: string) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
}

async function noSideScroll(page: Page) {
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, "no horizontal scroll").toBeLessThanOrEqual(width);
}

async function firstRun(page: Page, name: string, prefix: string, mobile = false) {
  await page.goto("/");
  await page.evaluate(() => { try { localStorage.clear(); } catch {} });
  await page.goto("/");
  await expect(page).toHaveURL(/\/onboarding$/);                      // no current candidate → first run

  // 1 · Add your proof
  await expect(page.getByText("Confirms your resume (Trust)")).toBeVisible();
  await expect(page.getByText("A manager reference is the strongest proof (Trust)")).toBeVisible();
  await shot(page, `${prefix}1-proof`);
  if (mobile) await noSideScroll(page);
  await page.getByLabel("Your name *").fill(name);
  await page.getByLabel("Resume *").setInputFiles(CV);
  await page.getByRole("button", { name: "Read my documents" }).click();

  // 2 · Target roles, picked while the documents are read
  await expect(page.getByText("Which roles are you aiming for?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save and exit" }).or(page.getByRole("link", { name: "Save and exit" }))).toBeVisible();
  await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: /Data Scientist/ }).click();
  await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: /AI Engineer/ }).click();
  await expect(page.getByTestId("role-card-baseline-data-scientist-entry")).toContainText("Primary");
  await expect(page.getByTestId("wait-line").filter({ hasText: "Your documents are read" })).toBeVisible({ timeout: 30_000 });
  await shot(page, `${prefix}2-roles`);
  if (mobile) await noSideScroll(page);
  await page.reload();                                                 // resumable: same step, same picks
  await expect(page.getByRole("group", { name: "Roles" }).getByRole("button", { name: /Data Scientist/ })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Continue to review" }).click();

  // 3 · Review
  await expect(page.getByText("Data Analyst — Acme Retail")).toBeVisible({ timeout: 30_000 });
  await shot(page, `${prefix}3-review`);
  if (mobile) await noSideScroll(page);
  await page.getByRole("button", { name: "Confirm profile" }).click();

  // 4 · Baseline scores: primary plan now, the other role's plan on first open
  const primary = page.getByTestId("score-card-baseline-data-scientist-entry");
  const other = page.getByTestId("score-card-baseline-ai-engineer-entry");
  await expect(primary.getByText("Build an A/B test project")).toBeVisible({ timeout: 60_000 });
  await expect(primary.getByText(/Percentile not available yet: fewer than \d+ other candidates have been scored for Data Scientist/)).toBeVisible();
  await expect(other.getByText("Your biggest gaps so far:", { exact: false })).toBeVisible();
  await expect(page.getByText("EEAT", { exact: true })).toHaveCount(1);
  await page.reload();                                                 // resumable
  await expect(primary).toBeVisible({ timeout: 30_000 });
  await shot(page, `${prefix}4-results`);
  if (mobile) await noSideScroll(page);
  await page.getByRole("button", { name: "Continue" }).click();

  // 5 · Finish your profile (optional)
  await expect(page.getByText("Voluntary self-identification", { exact: true })).toBeVisible();
  await expect(page.getByText("Answering is voluntary.", { exact: false })).toHaveCount(0);   // title only (specs/19)
  await expect(page.getByLabel("Gender")).toHaveValue("decline");
  await shot(page, `${prefix}5-details`);
  if (mobile) await noSideScroll(page);
}

test("first run: onboarding at 1440×900, then Home, My scores and Profile", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await firstRun(page, "Priya Natarajan", "desktop-");

  await page.getByLabel("Country you want to work in").fill("United States");
  await page.getByLabel("Authorized to work there").selectOption("yes");
  await page.getByLabel("Work mode preference").selectOption("hybrid");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByText("You're set up.")).toBeVisible();
  await shot(page, "desktop-6-done");
  await page.getByRole("button", { name: "Start applying" }).click();

  // Home: primary role strip, next best action; the finish card is gone (details saved)
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("Next best action")).toBeVisible();
  await expect(page.getByText("Build an A/B test project")).toBeVisible();
  await expect(page.getByText("Finish your profile")).toHaveCount(0);
  await expect(page.getByText(/Demo: viewing as Priya Natarajan/)).toBeVisible();
  await shot(page, "desktop-7-home");

  // My scores: the other role's plan is prepared on first open
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "My scores", exact: true }).click();
  await page.getByRole("tab", { name: /AI Engineer/ }).click();
  await page.getByRole("tab", { name: "Gaps and plan" }).click();
  await expect(page.getByText("Build an A/B test project").first()).toBeVisible({ timeout: 30_000 });
  await shot(page, "desktop-8-my-scores-plan");
  await page.getByRole("tab", { name: "Score" }).click();
  await expect(page.getByTestId("final-score")).toBeVisible();
  await shot(page, "desktop-9-my-scores");
  await expect(page.getByRole("link", { name: "What the market asks for" })).toBeVisible();

  // Profile: sources and application details (saved)
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile", exact: true }).click();
  await expect(page.getByText("Your proof")).toBeVisible();
  await expect(page.getByLabel("Country you want to work in")).toHaveValue("United States");
  await shot(page, "desktop-10-profile");

  // Switch profile
  await page.getByRole("button", { name: "Switch profile" }).click();
  await expect(page.getByRole("dialog", { name: "Switch profile" })).toBeVisible();
  await shot(page, "desktop-11-switch-profile");
  await page.keyboard.press("Escape");

  // Admin → Demo tools holds the old page; candidates no longer see it
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Demo tools" })).toHaveCount(0);
  await page.getByRole("link", { name: "Admin" }).click();
  await page.getByRole("link", { name: "Demo tools" }).click();
  await expect(page.getByRole("heading", { name: "Demo tools: score a profile against a role" })).toBeVisible();
  expect(problems).toEqual([]);
});

test("first run at 390 px: every step fits; Skip for now leaves the finish card on Home until dismissed", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await firstRun(page, "Sam Okafor", "mobile-", true);
  await page.getByRole("button", { name: "Skip for now" }).click();
  await expect(page.getByText("You're set up.")).toBeVisible();
  await shot(page, "mobile-6-done");
  await noSideScroll(page);
  await page.getByRole("button", { name: "Start applying" }).click();
  await expect(page.getByText("Next best action")).toBeVisible();
  await expect(page.getByText("Finish your profile")).toBeVisible();
  await shot(page, "mobile-7-home");
  await noSideScroll(page);
  await Promise.all([page.waitForResponse((r) => r.url().includes("/application-details/dismiss")),
    page.getByRole("button", { name: "Dismiss" }).click()]);
  await page.reload();
  await expect(page.getByText("Next best action")).toBeVisible();
  await expect(page.getByText("Finish your profile")).toHaveCount(0);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "My scores", exact: true }).click();
  await expect(page.getByTestId("final-score")).toBeVisible({ timeout: 30_000 });
  await shot(page, "mobile-8-my-scores");
  await noSideScroll(page);
});
