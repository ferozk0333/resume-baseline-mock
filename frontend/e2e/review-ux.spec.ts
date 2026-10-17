import fs from "node:fs";
import path from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";

// Spec 19: upload drop zone, form styling, the Review entry card, bullet editing, skills, section menu, the attention
// box, certifications after dedupe, More proof. Screenshots go to e2e/.tmp/spec19 (copied to docs/screenshots/spec19).
const OUT = "e2e/.tmp/spec19";
const CV = path.join(__dirname, ".tmp/cv.pdf");
const LINKEDIN = path.join(__dirname, ".tmp/linkedin-slow.pdf");
fs.mkdirSync(OUT, { recursive: true });

const shot = (page: Page, name: string, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });
const part = (l: Locator, name: string) => l.screenshot({ path: `${OUT}/${name}.png` });

async function noSideScroll(page: Page) {
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, "no horizontal scroll").toBeLessThanOrEqual(width);
}

async function paste(l: Locator, text: string) {
  await l.evaluate((el, t) => {
    const dt = new DataTransfer();
    dt.setData("text/plain", t);
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
  }, text);
}

async function toReview(page: Page, name: string, p: string, mobile: boolean) {
  await page.goto("/");
  await page.evaluate(() => { try { localStorage.clear(); } catch {} });
  await page.goto("/onboarding");
  await page.getByLabel("Your name *").fill(name);

  // Drop zone: empty, keyboard focus, wrong type, too large, filled, remove
  const zone = page.getByTestId("drop-resume");
  await expect(zone).toContainText("Drop your resume here or browse");
  await expect(page.getByText("No file chosen")).toHaveCount(0);
  await page.getByLabel("Your name *").press("Tab");
  await expect(page.getByLabel("Resume *")).toBeFocused();
  await part(page.locator("form").first().locator("[data-slot=card]").first(), `${p}a1-drop-zone-empty`);
  await page.getByLabel("Resume *").setInputFiles({ name: "photo.png", mimeType: "image/png", buffer: Buffer.from("x") });
  await expect(zone.getByRole("alert")).toHaveText("That's a PNG file. Upload a PDF or DOCX file.");
  await page.getByLabel("Resume *").setInputFiles({ name: "big.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(11 * 1024 * 1024) });
  await expect(zone.getByRole("alert")).toHaveText("That file is 11.0 MB. The limit is 10.0 MB.");
  await page.getByLabel("Resume *").setInputFiles(CV);
  await expect(page.getByTestId("resume-name")).toHaveText("cv.pdf");
  await page.getByRole("button", { name: "Remove Resume" }).click();
  await expect(zone).toContainText("Drop your resume here");
  await page.getByLabel("Resume *").setInputFiles(CV);
  await page.getByLabel("LinkedIn profile (PDF)").setInputFiles(LINKEDIN);
  await expect(page.getByRole("button", { name: "Replace Resume" }).or(page.getByText("Replace", { exact: false }).first())).toBeVisible();
  await part(page.locator("form").first().locator("[data-slot=card]").first(), `${p}a2-drop-zone-filled`);

  // Placeholders and field styling
  await expect(page.getByLabel("GitHub username or profile URL")).toHaveAttribute("placeholder", "your-username");
  await expect(page.getByLabel("Portfolio URL")).toHaveAttribute("placeholder", "https://your-site.com");
  const github = page.getByLabel("GitHub username or profile URL");
  expect(await github.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(255, 255, 255)");
  expect(await github.evaluate((el) => getComputedStyle(el, "::placeholder").color)).toBe("rgb(113, 117, 125)");
  await expect(page.getByText("Adding a credential ID or link makes a certificate count as stronger proof (Trust).")).toBeVisible();
  if (mobile) await noSideScroll(page);
  await page.getByRole("button", { name: "Read my documents" }).click();

  await page.getByRole("group", { name: "Roles" }).getByRole("button", { name: /Data Scientist/ }).click();
  await page.getByRole("button", { name: "Continue to review" }).click({ timeout: 30_000 });
  await expect(page.getByText("Data Analyst — Acme Retail").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("still-reading")).toHaveCount(0, { timeout: 30_000 });    // LinkedIn joined
}

async function review(page: Page, p: string, mobile: boolean) {
  const confirm = page.getByRole("button", { name: "Confirm profile" });

  // Section menu and the attention box
  const menu = page.getByTestId(mobile ? "section-menu-mobile" : "section-menu");
  if (mobile) {
    await expect(page.getByTestId("section-menu")).toBeHidden();
    await menu.getByLabel("Jump to section").selectOption("sec-skills");
    await expect(page.locator("#sec-skills")).toBeInViewport();
    await expect(menu.locator("option[value=sec-application]")).toHaveText(/Application details · After your scores/);
  } else {
    await expect(menu.getByRole("link", { name: /Roles/ })).toHaveAttribute("aria-current", "location");
    await menu.getByRole("link", { name: /Certifications/ }).click();
    await expect(menu.getByRole("link", { name: /Certifications/ })).toHaveAttribute("aria-current", "location");
    await expect(menu).toContainText("Application details");
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(800);                                                  // the smooth jump has settled
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await shot(page, `${p}b5-section-menu`);
  const attention = page.getByTestId("attention-box");
  await expect(attention).toContainText("Analytics Intern — Beta Corp: we only found the year. Please check the months.");
  await part(attention, `${p}b6-attention-box`);

  // Entry card: view → edit, one card at a time, unsaved-changes prompt, "Current" toggle
  const role = page.getByRole("article", { name: "Role: Data Analyst — Acme Retail" });
  await expect(role.getByText("Jan 2023 – Present")).toBeVisible();
  await expect(role.getByTestId("source-chips")).toContainText("Resume");
  await part(role, `${p}b1-card-view`);
  await role.getByRole("button", { name: "Edit Data Analyst — Acme Retail" }).click();
  await expect(role.getByLabel("Title")).toHaveValue("Data Analyst");
  await expect(role.getByLabel("End (month)")).toBeDisabled();                      // Current is ticked
  await expect(confirm).toBeDisabled();
  await expect(page.getByTestId("confirm-wait")).toHaveText("Save or cancel the card you're editing first.");
  await role.getByLabel("Location (optional)").fill("Seattle, WA");
  const intern = page.getByRole("article", { name: "Role: Analytics Intern — Beta Corp" });
  await intern.getByRole("button", { name: /^Edit / }).click();                      // another card while dirty
  await expect(page.getByRole("dialog")).toContainText("Discard your changes?");
  await page.getByRole("button", { name: "Keep editing" }).click();
  await expect(role).toHaveAttribute("data-editing", "true");
  await part(role, `${p}b2-card-edit`);

  // Bullets one by one: add, paste-split, reorder (buttons, keyboard, drag), delete
  const bullets = role.getByTestId("bullets-" + (await role.getAttribute("id"))!.replace("card-", ""));
  await expect(bullets.getByRole("textbox")).toHaveCount(2);
  await bullets.getByRole("button", { name: "Add bullet" }).click();
  await paste(bullets.getByRole("textbox").nth(2), "- Built a Looker dashboard for store managers.\n- Cut report time from 2 days to 2 hours.");
  await expect(bullets.getByRole("textbox")).toHaveCount(4);
  await expect(bullets.getByRole("textbox").nth(3)).toHaveValue("Cut report time from 2 days to 2 hours.");
  await bullets.getByRole("button", { name: "Move bullet 4 up" }).click();
  await expect(bullets.getByRole("textbox").nth(2)).toHaveValue("Cut report time from 2 days to 2 hours.");
  await bullets.getByRole("textbox").nth(2).press("Alt+ArrowUp");
  await expect(bullets.getByRole("textbox").nth(1)).toHaveValue("Cut report time from 2 days to 2 hours.");
  if (!mobile) {
    await bullets.getByTestId("drag-handle").nth(1).dragTo(bullets.getByTestId("bullet-row").nth(0));
    await expect(bullets.getByRole("textbox").nth(0)).toHaveValue("Cut report time from 2 days to 2 hours.");
  }
  await bullets.getByRole("button", { name: "Add bullet" }).click();               // an empty bullet is not saved
  await part(bullets, `${p}b3-bullet-editing`);
  await bullets.getByRole("button", { name: "Delete bullet 5" }).click();
  await bullets.getByRole("button", { name: "Add bullet" }).click();               // left empty: not saved
  await role.getByRole("button", { name: "Save" }).click();
  await expect(role).not.toHaveAttribute("data-editing", "true");
  await expect(role.getByText("Seattle, WA")).toBeVisible();
  await expect(role.locator("li").first()).toHaveText(mobile ? "Built a churn model in Python and scikit-learn that cut churn 12%."
    : "Cut report time from 2 days to 2 hours.");
  await expect(role.getByText("4 bullets")).toBeVisible();
  await expect(confirm).toBeEnabled();

  // Year-only dates: "The months are right" clears the attention box
  await intern.getByRole("button", { name: "The months are right" }).click();
  await expect(attention).toHaveCount(0);

  // Delete + Undo
  await intern.getByRole("button", { name: "Delete Analytics Intern — Beta Corp" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(intern).toHaveCount(0);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByRole("article", { name: "Role: Analytics Intern — Beta Corp" })).toBeVisible();

  // Skills: grouped, no kind prefix, search, add with a group, remove + Undo
  const skills = page.locator("#sec-skills");
  await skills.scrollIntoViewIfNeeded();
  await expect(skills.getByTestId("skill-group-Languages")).toContainText("Python");
  await expect(skills.getByTestId("skill-chip").first()).not.toContainText("tool");
  await part(skills, `${p}b4-skills-grouped`);
  await skills.getByLabel("Search skills and tools").fill("pyth");
  await expect(skills.getByTestId("skill-chip")).toHaveCount(1);
  await part(skills, `${p}b4-skills-searched`);
  await skills.getByLabel("Search skills and tools").fill("");
  await skills.getByLabel("Skill or tool").fill("dbt");
  await skills.getByLabel("Group").selectOption("Data");
  await skills.getByRole("button", { name: "Add skill" }).click();
  await expect(skills.getByTestId("skill-group-Data")).toContainText("dbt");
  await skills.getByRole("button", { name: "Remove dbt" }).click();
  await expect(skills.getByRole("button", { name: "Remove dbt" })).toHaveCount(0);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(skills.getByRole("button", { name: "Remove dbt" })).toBeVisible();

  // Certifications after dedupe: the resume's and LinkedIn's copy are one card with both sources
  const certs = page.locator("#sec-certifications");
  await expect(certs.getByTestId("certification-card")).toHaveCount(1);
  await expect(certs.getByTestId("source-chips")).toHaveText(/Resume.*LinkedIn/);
  await expect(certs).toContainText("Credential ID added");
  await expect(certs).not.toContainText(/verified/i);
  await part(certs, `${p}c1-certifications-after-dedupe`);

  // More proof: collapsed chips, then sections
  const more = page.locator("#sec-more");
  await expect(more.getByTestId("proof-add-chips")).toBeVisible();
  await part(more, `${p}d1-more-proof-collapsed`);
  await more.getByRole("button", { name: "Add publication" }).click();
  const pub = more.getByTestId("proof-card-publication");
  await pub.getByLabel("Title", { exact: true }).fill("Forecasting store demand with Prophet");
  await pub.getByLabel("Venue").fill("PyData Seattle");
  await pub.getByLabel("Year").fill("2024");
  await pub.getByRole("button", { name: "Save" }).click();
  await expect(more.getByTestId("proof-publication")).toContainText("Forecasting store demand with Prophet — PyData Seattle");
  await more.getByRole("button", { name: "Add language" }).click();
  const lang = more.getByTestId("proof-card-language");
  await lang.getByLabel("Language", { exact: true }).fill("Spanish");
  await lang.getByLabel("Proficiency").fill("Conversational");
  await lang.getByRole("button", { name: "Save" }).click();
  await expect(more.getByTestId("proof-language")).toContainText("Shown to recruiters, not scored");
  await part(more, `${p}d2-more-proof-open`);
  if (mobile) await noSideScroll(page);
  await shot(page, `${p}review-full`, true);
  await expect(confirm).toBeEnabled();
}

test("spec 19 Review at 1440×900", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await toReview(page, "Review UX Desktop", "desktop-", false);
  await review(page, "desktop-", false);
  expect(problems).toEqual([]);
});

test("spec 19 Review at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await toReview(page, "Review UX Mobile", "mobile-", true);
  await review(page, "mobile-", true);
});
