import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// specs/14: create a job by upload, copy and scratch. The API runs on fakes with JOB_EXTRACT_FAKE=1: the
// Brightwater JD gets a scripted extraction (no model call); anything else falls back to the manual form.
const JD_PDF = path.resolve(__dirname, "../../tests/fixtures/jd/AI_Engineer_JD_Brightwater.pdf");

async function next(page: Page) {
  await page.getByRole("button", { name: "Next", exact: true }).click();
}

test("upload a JD → steps 2–4 → publish → candidate applies", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/recruiter");
  await expect(page.getByRole("table", { name: "Open jobs" })).toContainText("Data Scientist");
  await page.screenshot({ path: "e2e/.tmp/job-0-jobs-page.png" });
  await page.getByRole("table", { name: "Open jobs" }).getByRole("cell", { name: "Northwind Analytics" }).click();   // whole row
  await expect(page).toHaveURL(/\/recruiter\/jobs\/[^/]+$/);
  await page.goto("/recruiter");
  await page.getByRole("link", { name: "Create job" }).click();
  await page.getByLabel("File").setInputFiles(JD_PDF);
  await page.screenshot({ path: "e2e/.tmp/job-1-start.png" });
  await page.getByRole("button", { name: "Read job description" }).click();

  // Step 2: filled from the JD, with the source on hover and the JD alongside
  await expect(page.getByLabel(/^Title/)).toHaveValue("AI Engineer", { timeout: 20_000 });
  await expect(page.getByLabel(/^Company/)).toHaveValue("Brightwater Systems");
  await expect(page.getByLabel(/^Minimum years/)).toHaveValue("1");
  await expect(page.getByRole("complementary", { name: "Job description" })).toContainText("Minimum qualifications");
  const table = page.getByRole("table", { name: "Requirements" });
  await expect(table.getByLabel("Requirement 1 name")).toHaveValue("Python");
  await expect(page.getByText(/left out/)).toContainText("Kubernetes: quote not found");
  await expect(table.getByRole("checkbox", { name: /must-have/, checked: true })).toHaveCount(4);
  await page.screenshot({ path: "e2e/.tmp/job-2-role.png", fullPage: true });
  await next(page);

  // Step 3
  await expect(page.getByLabel("What will this person own?")).not.toHaveValue("");
  await expect(page.getByLabel("How far does their work reach?")).toHaveValue("3");
  await page.getByRole("radio", { name: "Startup" }).click();
  await page.screenshot({ path: "e2e/.tmp/job-3-setup.png", fullPage: true });
  await next(page);

  // Step 4: review with the candidate preview; autosave lets us leave and come back to this step
  const preview = page.getByRole("article", { name: "Candidate preview" });
  await expect(preview).toContainText("Brightwater Systems");
  await expect(preview).toContainText("pgvector");
  await expect(page.getByText("Draft saved")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/job-4-review.png", fullPage: true });
  const url = page.url();
  await page.goto("/recruiter");
  await page.getByRole("tab", { name: /Drafts/ }).click();
  await expect(page.getByRole("table", { name: "Drafts jobs" })).toContainText("Draft · step 4 of 4");
  await page.screenshot({ path: "e2e/.tmp/job-5-drafts.png" });
  await page.goto(url);
  await expect(page.getByRole("article", { name: "Candidate preview" })).toBeVisible();
  await page.getByRole("button", { name: "Publish", exact: true }).click();

  // Job page: no applicants yet; Job description tab
  await expect(page.getByText("No applicants yet.")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Job description" }).click();
  await expect(page.getByText("Original job description")).toBeVisible();
  await page.screenshot({ path: "e2e/.tmp/job-6-published-jd-tab.png" });

  // Candidate side: the job is open for applications
  await page.getByRole("link", { name: "Candidate" }).click();
  await expect(page.getByRole("list", { name: "Open jobs" })).toContainText("AI Engineer");
  await page.getByRole("list", { name: "Open jobs" }).getByRole("button", { name: /AI Engineer/ }).first().click();
  await page.screenshot({ path: "e2e/.tmp/job-7-candidate-apply.png", fullPage: true });
  expect(problems).toEqual([]);
});

test("copy an existing job and start from scratch", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/recruiter/jobs/new");
  await page.getByRole("combobox", { name: "Job to copy" }).selectOption({ label: "Data Scientist · Northwind Analytics" });
  await page.getByRole("button", { name: "Copy and edit" }).click();
  await expect(page.getByLabel(/^Title/)).toHaveValue("Data Scientist (copy)", { timeout: 20_000 });
  await expect(page.getByRole("table", { name: "Requirements" }).getByLabel("Requirement 1 name")).not.toHaveValue("");

  await page.goto("/recruiter/jobs/new");
  await page.getByRole("button", { name: "Start from scratch" }).click();
  await expect(page.getByLabel(/^Title/)).toHaveValue("", { timeout: 20_000 });
  await page.getByLabel(/^Title/).fill("Analytics Engineer");
  await page.getByLabel(/^Company/).fill("Acme");
  await page.getByRole("button", { name: "Add requirement" }).click();
  await page.getByLabel("Requirement 1 name").fill("dbt");
  await next(page);
  await next(page);
  await expect(page.getByRole("list", { name: "Warnings" })).toContainText("What good looks like");
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("No applicants yet.")).toBeVisible({ timeout: 20_000 });

  // Edit a published job: details change → saved without re-scoring
  await page.getByRole("link", { name: "Edit job" }).click();
  await page.getByLabel("Location").fill("Remote");
  await next(page);
  await next(page);
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("No applicants yet.")).toBeVisible({ timeout: 20_000 });
});
