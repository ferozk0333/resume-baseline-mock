import fs from "node:fs";
import { expect, test } from "@playwright/test";

// Market baselines after the cleanup round: fixtures from Demo tools → one Recompute → publish (no minimum data) →
// insights → toggle; a later draft and a discarded one (History). Screenshots also go to e2e/.tmp/cleanup.
fs.mkdirSync("e2e/.tmp/cleanup", { recursive: true });
test("market baseline from the test fixtures", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/demo");
  await page.getByRole("button", { name: "Load test fixtures: AI Engineer" }).click();
  await expect(page.getByText(/AI Engineer: \d+ test postings? loaded/)).toBeVisible();
  await page.goto("/admin/market");
  await expect(page.getByRole("button", { name: /Load test fixtures/ })).toHaveCount(0);       // moved to Demo tools
  await expect(page.getByText(/Allow test versions|placeholders|config\//)).toHaveCount(0);
  await page.getByRole("button", { name: "Fetch now" }).click();
  await expect(page.getByRole("status", { name: "Fetch result" })).toContainText("No company has a public job board yet");
  const postings = page.getByRole("region", { name: "Postings in the window" });
  await expect(postings).toContainText("Lumenfield");
  await expect(postings.locator("thead th")).toHaveText(["Company", "Tier", "Title", "Level", "Last seen"]);
  await expect(page.getByRole("button", { name: /Recompute/ })).toHaveCount(1);
  await page.getByRole("button", { name: "Recompute" }).click();
  const versions = page.getByRole("region", { name: "Versions" });
  await expect(versions).toContainText("Based on 3 postings from 3 companies");
  await expect(versions).not.toContainText(/Insufficient|test version/i);
  await page.screenshot({ path: "e2e/.tmp/market-1-role.png", fullPage: true });
  await versions.getByRole("link", { name: "Version 1" }).click();
  await expect(page.getByRole("table", { name: "Market criteria" })).toContainText("LLM application development");
  await expect(page.getByRole("button", { name: "Publish" })).toBeEnabled();
  await page.getByRole("button", { name: "Publish" }).click();
  await expect(page.getByText("active", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/market-2-insights-fixtures.png", fullPage: true });
  await page.getByRole("main").getByRole("link", { name: "Market baselines" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).not.toContainText("Jobs");     // Admin mode
  // The jobs test scores AI Engineer candidates first: the switch then asks to confirm the re-score
  await page.getByRole("radio", { name: /Market v1/ }).click();
  const confirm = page.getByRole("dialog", { name: "Switch the baseline source?" });
  if (await confirm.isVisible().catch(() => false) || await confirm.waitFor({ timeout: 1500 }).then(() => true, () => false)) {
    await expect(confirm).not.toContainText("model");
    await confirm.getByRole("button", { name: "Switch" }).click();
  }
  await expect(page.getByRole("radio", { name: /Market v1/ })).toHaveAttribute("aria-checked", "true");
  await page.screenshot({ path: "e2e/.tmp/market-3-toggle.png" });
  await page.getByRole("radio", { name: "Manual" }).click();
  await expect(page.getByRole("radio", { name: "Manual" })).toHaveAttribute("aria-checked", "true");

  // Versions: the active one and the latest draft; older ones under a collapsed History
  await page.getByRole("button", { name: "Recompute" }).click();                         // v2 draft
  await expect(versions.getByRole("link", { name: "Version 2" })).toBeVisible();
  await versions.getByRole("link", { name: "Version 2" }).click();
  await page.getByRole("button", { name: "Discard" }).click();
  await expect(page.getByText("discarded", { exact: true }).first()).toBeVisible();
  await page.getByRole("main").getByRole("link", { name: "Market baselines" }).click();
  await page.getByRole("button", { name: "Recompute" }).click();                         // v3 draft
  await expect(versions.getByRole("link", { name: "Version 3" })).toBeVisible();
  const history = versions.getByTestId("version-history");
  await expect(history).toContainText("History (1)");
  await expect(history.getByRole("link", { name: "Version 2" })).toBeHidden();             // collapsed
  await page.waitForTimeout(4500);                                                        // toasts close
  await page.screenshot({ path: "e2e/.tmp/cleanup/market-baselines.png", fullPage: true });
  await history.locator("summary").click();
  await expect(history.getByRole("link", { name: "Version 2" })).toBeVisible();
  await versions.screenshot({ path: "e2e/.tmp/cleanup/market-versions-history-open.png" });
  expect(problems).toEqual([]);
});

// Candidate side: "What the market asks for" (counts only) and the Score page's "Scored against" switch.
test("candidate sees the market view and switches a score to the Market baseline", async ({ page, request }) => {
  const API = process.env.E2E_API ?? "http://localhost:8010";
  await page.setViewportSize({ width: 1440, height: 900 });
  await request.post(`${API}/market/data_scientist/fixtures`);
  const v = await (await request.post(`${API}/market/data_scientist/recompute`, { data: {} })).json();
  await request.post(`${API}/market/versions/${v.id}/publish`);

  await page.goto("/market");
  await page.getByRole("tab", { name: "Data Scientist" }).click();
  await expect(page.getByRole("region", { name: "Market criteria" })).toContainText("SQL");
  await expect(page.getByText(/Based on 3 postings from 3 companies/)).toBeVisible();
  await expect(page.getByText(/Test version|insufficient/i)).toHaveCount(0);
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
  await expect(page.getByTestId("market-basis")).toHaveText("Market: Based on 3 postings from 3 companies");
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
