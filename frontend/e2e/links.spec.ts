import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { hrefFor } from "../lib/links";

// Audit round, item 4: no screen breaks a link. Every link to a stored URL goes through ExtLink, whose href is the
// stored URL exactly (data-source-url keeps the original for this check). Candidate profile, recruiter panel and
// Admin pages, for the seeded test profiles plus one with hard URLs added through Review.
const API = process.env.E2E_API ?? "http://localhost:8010";
const HARD = {
  lancet: "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736(26)01714-9/abstract",
  cert: "https://www.credly.com/badges/8c0a3fb1-e88c-4706-94e5-93991c08a908/linked_in_profile?src=a%2Fb&x=1#top",
  doi: "https://doi.org/10.1016/S0140-6736(20)30183-5",
};

type Shown = { href: string | null; src: string | null; text: string };

async function shownLinks(page: Page): Promise<{ withSource: Shown[]; bare: string[] }> {
  const withSource = await page.locator("a[data-source-url]").evaluateAll((els) =>
    els.map((e) => ({ href: e.getAttribute("href"), src: e.getAttribute("data-source-url"), text: e.textContent ?? "" })));
  // An external link that did not come through ExtLink (the API's own file links and in-page anchors excepted).
  const bare = await page.locator("a[href^='http']:not([data-source-url])").evaluateAll((els, api) =>
    els.filter((e) => e.getRootNode() === document)          // Next.js dev tools live in a shadow root: not the app
      .map((e) => e.getAttribute("href") ?? "").filter((h) => !h.startsWith(api as string)), API);
  return { withSource, bare };
}

function expectExact(links: Shown[]) {
  for (const l of links) expect(l.href, `href of ${l.src}`).toBe(hrefFor(l.src ?? ""));
}

async function storedUrls(request: APIRequestContext, cid: string): Promise<string[]> {
  const p = (await (await request.get(`${API}/candidates/${cid}/profile`)).json()).profile;
  return [...p.projects.map((x: { url: string | null }) => x.url), ...p.certifications.map((x: { verify_url: string | null }) => x.verify_url)]
    .filter(Boolean);
}

test("every rendered link is its stored URL, character for character", async ({ page, request }) => {
  test.setTimeout(240_000);
  const cands: { id: string; name: string }[] = await (await request.get(`${API}/candidates`)).json();
  const lena = cands.find((c) => c.name === "Lena Park")!;
  for (const [item, data] of [
    ["project", { kind: "publication", name: "Lancet Commission (audit link test)", url: HARD.lancet }],
    ["project", { kind: "publication", name: "DOI link test", url: HARD.doi }],
    ["certification", { name: "Credly link test", issuer: "Test", verify_url: HARD.cert }],
  ] as const) {
    const r = await request.patch(`${API}/candidates/${lena.id}/profile`, { data: { op: "add", item, data } });
    expect(r.ok()).toBeTruthy();
  }

  // Candidate side: the Review step (/onboarding), where every item shows its link. Each profile's onboarding step
  // is put back afterwards.
  const openReview = async (cid: string) => {
    const ob = await (await request.get(`${API}/candidates/${cid}/onboarding`)).json();
    if (!ob.targets.length) {
      const r = await request.put(`${API}/candidates/${cid}/targets`, { data: { baseline_ids: [ob.roles[0].baseline_id] } });
      expect(r.ok()).toBeTruthy();
    }
    expect((await request.put(`${API}/candidates/${cid}/onboarding`, { data: { step: "review" } })).ok()).toBeTruthy();
    await page.goto("/");
    await page.evaluate((id) => localStorage.setItem("rightstaff.currentCandidate", id), cid);
    await page.goto("/onboarding");
    await page.getByRole("article").first().waitFor({ timeout: 20_000 });
    await page.waitForLoadState("networkidle");
    const closed = page.locator("article button[aria-expanded='false']");      // every card open, links shown
    while (await closed.count()) await closed.first().click();
    return ob.onboarding.step as string;
  };
  let checked = 0;
  for (const c of cands) {
    const stored = await storedUrls(request, c.id);
    if (!stored.length) continue;
    const step = await openReview(c.id);
    const { withSource, bare } = await shownLinks(page);
    expectExact(withSource);
    expect(bare, `links not built by ExtLink on ${c.name}'s profile`).toEqual([]);
    for (const url of stored) {
      expect(withSource.some((l) => l.src === url && l.href === hrefFor(url)), `${c.name}: ${url}`).toBeTruthy();
    }
    if (c.id === lena.id) {      // the hard URLs are shown exactly: parentheses, %XX, ?, & and # untouched
      for (const url of Object.values(HARD)) await expect(page.locator(`a[href="${url}"]`).first()).toBeAttached();
    }
    checked += stored.length;
    await request.put(`${API}/candidates/${c.id}/onboarding`, { data: { step } });
  }
  expect(checked).toBeGreaterThan(10);

  // Recruiter: every applicant's Overview and Work tabs.
  const jobs: { id: string }[] = await (await request.get(`${API}/jobs`)).json();
  for (const j of jobs) {
    const apps: { application_id: string }[] = (await (await request.get(`${API}/jobs/${j.id}/applicants`)).json()).applicants ?? [];
    for (const a of apps.slice(0, 20)) {
      for (const tab of ["overview", "work"]) {
        await page.goto(`/recruiter/jobs/${j.id}?app=${a.application_id}&tab=${tab}`);
        await page.getByRole("tab", { name: /Overview/ }).first().waitFor();
        await page.waitForLoadState("networkidle");
        const { withSource, bare } = await shownLinks(page);
        expectExact(withSource);
        expect(bare, `recruiter ${tab} tab`).toEqual([]);
      }
    }
  }

  // Admin
  for (const path of ["/admin/market", "/admin/screening", "/admin/demo"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const { withSource, bare } = await shownLinks(page);
    expectExact(withSource);
    expect(bare, path).toEqual([]);
  }
});
