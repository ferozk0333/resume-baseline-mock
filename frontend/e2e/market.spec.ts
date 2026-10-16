import { expect, test } from "@playwright/test";

// specs/15: fixtures → recompute without a model → allow test versions → publish → insights → toggle warning.
test("market baseline from the test fixtures", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/market");
  await page.getByRole("button", { name: "Fetch now" }).click();               // placeholders only: says so
  await expect(page.getByRole("status", { name: "Fetch result" })).toContainText("Nothing to fetch");
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
  await page.getByRole("main").getByRole("link", { name: "Market baselines" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).not.toContainText("Jobs");     // Admin mode
  // No AI Engineer candidate is scored in the e2e demo, so the switch needs no confirmation (tests cover the warning)
  await page.getByRole("radio", { name: /Market v1/ }).click();
  await expect(page.getByRole("radio", { name: /Market v1/ })).toHaveAttribute("aria-checked", "true");
  await page.screenshot({ path: "e2e/.tmp/market-3-toggle.png" });
  await page.getByRole("radio", { name: "Manual" }).click();
  await expect(page.getByRole("radio", { name: "Manual" })).toHaveAttribute("aria-checked", "true");
  expect(problems).toEqual([]);
});

// Candidate side: "What the market asks for" (counts only) and the Score page's "Scored against" switch.
test("candidate sees the market view and switches a score to the Market baseline", async ({ page, request }) => {
  const API = "http://localhost:8010";
  await page.setViewportSize({ width: 1440, height: 900 });
  await request.post(`${API}/market/data_scientist/fixtures`);
  const v = await (await request.post(`${API}/market/data_scientist/recompute`, { data: { test: true } })).json();
  await request.put(`${API}/market/settings`, { data: { allow_test_versions: true } });
  await request.post(`${API}/market/versions/${v.id}/publish`);

  await page.goto("/market");
  await page.getByRole("tab", { name: "Data Scientist" }).click();
  await expect(page.getByRole("region", { name: "Market criteria" })).toContainText("SQL");
  await expect(page.getByText("Test version – insufficient data")).toBeVisible();
  await expect(page.getByRole("region", { name: "Market criteria" })).not.toContainText("Lumenfield");
  await page.screenshot({ path: "e2e/.tmp/market-4-candidate-view.png", fullPage: true });

  // the Data Scientist run from the candidate-flow test
  const cands = await (await request.get(`${API}/candidates`)).json();
  let runId = "";
  for (const c of cands) {
    const runs = await (await request.get(`${API}/candidates/${c.id}/runs`)).json();
    const ds = runs.find((r: { baseline_id: string; status: string }) => r.baseline_id === "baseline-data-scientist-entry" && r.status === "done");
    if (ds) { runId = ds.id; break; }
  }
  test.skip(!runId, "needs the candidate-flow run");
  await page.goto(`/runs/${runId}`);
  const sw = page.getByRole("radiogroup", { name: "Scored against" });
  await expect(sw.getByRole("radio", { name: "Standard baseline" })).toHaveAttribute("aria-checked", "true");
  await page.screenshot({ path: "e2e/.tmp/market-5-score-switch.png" });
  await sw.getByRole("radio", { name: /Market baseline \(v1/ }).click();
  await expect(page.getByRole("dialog")).toContainText("Re-score against Market baseline (v1");
  await page.waitForTimeout(400);                                          // dialog fade-in
  await page.screenshot({ path: "e2e/.tmp/market-6-rescore-notice.png" });
  await page.getByRole("button", { name: "Re-score" }).click();
  await expect(page.getByRole("radiogroup", { name: "Scored against" }).getByRole("radio", { name: /Market baseline/ }))
    .toHaveAttribute("aria-checked", "true", { timeout: 30_000 });
  await page.screenshot({ path: "e2e/.tmp/market-7-scored-on-market.png" });
});
