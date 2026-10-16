import path from "node:path";
import { expect, test } from "@playwright/test";

test("upload → review → score → plan", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: /^Data Scientist entry/ }).click();       // the target role, not an open job
  await page.getByLabel("Name").fill("Jane Doe");
  await page.screenshot({ path: "e2e/.tmp/home.png", fullPage: true });
  await page.getByRole("button", { name: "Start" }).click();

  // 1 · Upload
  await expect(page).toHaveURL(/\/candidates\/.+\/onboard/);
  await page.getByLabel("Resume *").setInputFiles(path.join(__dirname, ".tmp/cv.pdf"));
  await page.screenshot({ path: "e2e/.tmp/upload.png", fullPage: true });
  await page.getByRole("button", { name: "Read my documents" }).click();

  // 2 · Review
  await expect(page.getByText("Data Analyst — Acme Retail")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Dropped items (0)")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/review.png", fullPage: true });
  await page.getByRole("button", { name: "Confirm & score" }).click();

  // 3 · Score
  await expect(page).toHaveURL(/\/runs\/[^/]+$/, { timeout: 60_000 });
  const final = page.getByTestId("final-score");
  await expect(final).toHaveText(/\d+\.\d/);
  await expect(page.getByText("demo pool")).toBeVisible();
  // Scripted (FakeJudge) runs never enter a pool, and say so.
  await expect(page.getByText("This run used scripted test labels").first()).toBeVisible();
  await page.getByRole("button", { name: /Ownership/ }).click();
  await expect(page.getByText("How it is scored:").first()).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/score.png", fullPage: true });

  // Weights drawer: moving a slider re-scores without leaving the page
  const before = await final.textContent();
  await page.getByRole("button", { name: "Weights" }).click();
  const authority = page.getByRole("slider").nth(2);
  await authority.focus();
  for (let i = 0; i < 20; i++) await page.keyboard.press("ArrowLeft");
  await expect(final).not.toHaveText(before ?? "", { timeout: 10_000 });
  await page.screenshot({ path: "e2e/.tmp/weights.png" });
  await page.keyboard.press("Escape");

  // Plan
  await page.getByRole("link", { name: "Gaps and plan" }).click();
  await expect(page.getByText("Build an A/B test project")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Ranked gaps")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/plan.png", fullPage: true });
  await page.getByRole("link", { name: "Baselines" }).click();
  await expect(page.getByRole("cell", { name: /Data Scientist/ })).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/baselines.png", fullPage: true });
  expect(problems).toEqual([]);
});
