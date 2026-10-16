import { expect, test } from "@playwright/test";

// specs/15: fixtures → recompute without a model → allow test versions → publish → insights → toggle warning.
test("market baseline from the test fixtures", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/recruiter/market");
  await page.getByRole("button", { name: "Load test fixtures" }).click();
  await expect(page.getByRole("region", { name: "Postings in the window" })).toContainText("Lumenfield");
  await page.getByRole("button", { name: "Recompute without a model" }).click();
  await expect(page.getByRole("region", { name: "Versions" })).toContainText("Insufficient data");
  await page.getByLabel(/Allow test versions/).click();
  await expect(page.getByLabel(/Allow test versions/)).toBeChecked();
  await page.screenshot({ path: "e2e/.tmp/market-1-role.png", fullPage: true });
  await page.getByRole("link", { name: "Version 1" }).click();
  await expect(page.getByRole("table", { name: "Market criteria" })).toContainText("LLM application development");
  await page.getByRole("button", { name: "Publish" }).click();
  await expect(page.getByText("active", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/market-2-insights-fixtures.png", fullPage: true });
  await page.getByRole("link", { name: "Market baselines" }).click();
  // No AI Engineer candidate is scored in the e2e demo, so the switch needs no confirmation (tests cover the warning)
  await page.getByRole("radio", { name: /Market v1/ }).click();
  await expect(page.getByRole("radio", { name: /Market v1/ })).toHaveAttribute("aria-checked", "true");
  await page.screenshot({ path: "e2e/.tmp/market-3-toggle.png" });
  await page.getByRole("radio", { name: "Manual" }).click();
  await expect(page.getByRole("radio", { name: "Manual" })).toHaveAttribute("aria-checked", "true");
  expect(problems).toEqual([]);
});
