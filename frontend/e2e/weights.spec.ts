import fs from "node:fs";
import { expect, test } from "@playwright/test";

// Spec 21: the weight assistant in Job settings, on the fake API (the model is a hand-written stand-in answer).
// Describe → Suggest → change table, ignored notice and ranking preview → Apply → Undo → history.
const API = process.env.E2E_API ?? "http://localhost:8010";
const OUT = "e2e/.tmp/spec21";
fs.mkdirSync(OUT, { recursive: true });
const TEXT = "Young, energetic native English speakers who are a great culture fit; strong Python and SQL.";

test("weight assistant: suggest, preview, apply, undo, history", async ({ page, request }) => {
  const jobs: { id: string; title: string; weights: unknown }[] = await (await request.get(`${API}/jobs`)).json();
  const job = jobs.find((j) => j.title === "Data Scientist")!;          // its applicants are scored in the e2e seed
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/recruiter/jobs/${job.id}/settings`);
  const box = page.getByLabel("Describe what you're looking for in this role");
  await box.fill(TEXT);
  await expect(page.getByText(`${TEXT.length}/800`)).toBeVisible();
  const assistant = page.locator("section", { has: page.getByRole("heading", { name: "Suggest weights from a description" }) });
  await assistant.screenshot({ path: `${OUT}/1-text-box.png` });

  await page.getByRole("button", { name: "Suggest weights" }).click();
  const card = page.getByRole("article", { name: "Suggested weights" });
  await expect(card.getByText("Suggested by AI · you decide")).toBeVisible();
  await expect(card.getByTestId("wa-ignored")).toContainText("can't be used for ranking and were ignored");
  await expect(card.getByTestId("wa-ignored")).toContainText("culture fit");
  await expect(card.getByTestId("wa-changes")).toContainText("Python and SQL");
  await expect(card.getByText(/No change \(\d+\)/)).toBeVisible();
  const preview = card.getByTestId("wa-preview");
  await expect(preview.getByText(/move up/)).toBeVisible();
  expect(await preview.locator("tbody tr").count()).toBeGreaterThan(1);
  await card.screenshot({ path: `${OUT}/2-suggestion.png` });
  // A description that reorders applicants, for the preview
  await box.fill("Senior people who've led teams and owned delivery end to end.");
  await page.getByRole("button", { name: "Suggest weights" }).click();
  await expect(card.getByTestId("wa-changes")).toContainText("Ownership");
  await preview.screenshot({ path: `${OUT}/3-preview.png` });
  await box.fill(TEXT);
  await page.getByRole("button", { name: "Suggest weights" }).click();
  await expect(card.getByTestId("wa-ignored")).toBeVisible();

  // Nothing changed yet (HITL)
  expect((await (await request.get(`${API}/jobs/${job.id}`)).json()).weights).toEqual(job.weights);
  await card.getByRole("button", { name: "Apply weights" }).click();
  const applied = page.getByRole("status").filter({ hasText: "Weights applied" }).first();
  await expect(applied).toBeVisible();
  const after = (await (await request.get(`${API}/jobs/${job.id}`)).json()).weights;
  expect(after).not.toEqual(job.weights);
  await assistant.screenshot({ path: `${OUT}/4-applied-undo.png` });
  await applied.getByRole("button", { name: "Undo" }).click();
  await expect.poll(async () => (await (await request.get(`${API}/jobs/${job.id}`)).json()).weights).toEqual(job.weights);

  const history = page.getByTestId("wa-history");
  await history.locator("summary").click();
  await expect(history.getByRole("listitem").first()).toContainText("Undone");
  await assistant.screenshot({ path: `${OUT}/5-history.png` });
  await history.getByRole("button", { name: "Open" }).first().click();
  await expect(card.getByText("Suggested by AI · you decide")).toBeVisible();
  await card.getByRole("button", { name: "Discard" }).click();
  await expect(card).toHaveCount(0);

  // The 390 px layout fits
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByRole("button", { name: "Suggest weights" }).click();
  await expect(card).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
