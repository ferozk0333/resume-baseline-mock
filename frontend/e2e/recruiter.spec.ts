import { expect, test, type Locator } from "@playwright/test";

// The applicant list is virtualized: rows exist only near the scroll position. Scroll until one appears.
async function reveal(list: Locator, row: Locator) {
  for (let y = 0; y <= 20_000 && (await row.count()) === 0; y += 300) {
    await list.evaluate((el, top) => { el.scrollTop = top; el.dispatchEvent(new Event("scroll")); }, y);
  }
  return row.first();
}

test("open job → select applicant → panel → compare 2 → shortlist", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(e.message));

  await page.goto("/recruiter");
  await page.getByRole("link", { name: /Data Scientist/ }).click();
  const list = page.getByRole("list", { name: "Applicants" });
  await expect(list.getByRole("listitem").first()).toContainText("Strong early-career DS", { timeout: 20_000 });

  // Profile panel of the top applicant loads
  const panel = page.getByRole("article", { name: /Profile of Strong early-career DS/ });
  await expect(panel.getByText("Requirements vs. evidence")).toBeVisible();
  await expect(panel.getByText("Why this candidate")).toBeVisible();
  await expect(panel.getByText("Demo text").first()).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/recruiter-job.png", fullPage: false });

  // A parsed demo profile shows its work before scoring
  await (await reveal(list, list.getByRole("button", { name: /^Sofia Lindqvist/ }))).click();
  await expect(page.getByRole("article", { name: /Profile of Sofia Lindqvist/ }).getByText("Not scored yet")).toBeVisible();

  // Compare 2 (the list is virtualized: scroll back to the top rows first)
  await list.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); });
  await list.getByRole("checkbox", { name: "Compare Strong early-career DS" }).check();
  await list.getByRole("checkbox", { name: "Compare Project-strong grad" }).check();
  await page.getByRole("button", { name: /Compare \(2\)/ }).click();
  await expect(page.getByRole("heading", { name: "Compare applicants" })).toBeVisible();
  await expect(page.getByText("Top evidence · Project-strong grad")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/recruiter-compare.png", fullPage: true });

  // Shortlist one
  await page.getByRole("link", { name: "Applicants" }).click();
  await list.getByRole("button", { name: /^Project-strong grad/ }).click();
  const grad = page.getByRole("article", { name: /Profile of Project-strong grad/ });
  await grad.getByRole("button", { name: "Shortlist" }).click();
  await expect(grad.getByRole("button", { name: "Shortlisted" })).toBeVisible();
  await expect(list.getByRole("listitem").filter({ hasText: "Project-strong grad" })).toContainText("Shortlisted");
  await page.screenshot({ path: "e2e/.tmp/recruiter-panel.png", fullPage: false });

  // Job settings: must-haves and weights
  await page.getByRole("link", { name: "Job settings" }).click();
  await expect(page.getByText("Must-haves do not change the score.")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/recruiter-settings.png", fullPage: true });
  expect(problems).toEqual([]);
});
