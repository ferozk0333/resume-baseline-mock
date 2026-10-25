"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { FileDrop } from "@/components/ui/file-drop";
import { Check, Download, Loader2, Plus, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, get, post, useUploadLimit, type MarketPosting, type MarketRole, type MarketVersion, type Suggestion } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ExtLink } from "@/components/shared/ext-link";

type FetchResult = { boards: number; seen: number; new: number; excluded_titles: number; closed: number; errors: string[];
  skipped: number };
type Overview = { roles: MarketRole[]; thresholds: Record<string, number> };

/** Market baselines (specs/15): per role, postings in the window, versions, and the Manual/Market toggle. */
export default function MarketPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [role, setRole] = useState<string>("ai_engineer");
  const [busy, setBusy] = useState<string | null>(null);
  const [fetched, setFetched] = useState<(FetchResult & { role: string }) | null>(null);
  const [switching, setSwitching] = useState<{ role: string; to: string; candidates: number; calls: number } | null>(null);
  const load = useCallback(() => { get<Overview>("/market/roles").then(setData).catch((e) => toast.error(e.message)); }, []);
  useEffect(() => { load(); }, [load]);

  async function run(label: string, p: () => Promise<unknown>, ok?: string) {
    setBusy(label);
    try { await p(); if (ok) toast.success(ok); load(); } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  }
  async function toggle(r: MarketRole, to: "manual" | "market", confirm = false) {
    try {
      await post(`/market/${r.key}/source`, { source: to, confirm });
      setSwitching(null); toast.success(`${r.title}: ${to === "market" ? "Market" : "Manual"} baseline`); load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const d = JSON.parse(e.message);
        setSwitching({ role: r.key, to, candidates: d.candidates, calls: d.calls });
      } else toast.error((e as Error).message);
    }
  }

  if (!data) return <Skeleton className="h-96" />;
  const r = data.roles.find((x) => x.key === role)!;
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">Market baselines</h1>
      <div role="tablist" aria-label="Role" className="flex w-fit rounded-lg border bg-muted p-0.5 text-sm">
        {data.roles.map((x) => (
          <button key={x.key} role="tab" type="button" aria-selected={role === x.key} onClick={() => setRole(x.key)}
            className={cn("rounded-md px-3 py-1", role === x.key ? "bg-white shadow-sm" : "text-muted-foreground")}>{x.title}</button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Baseline source</CardTitle>
            <CardDescription>Default for new scores.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div role="radiogroup" aria-label="Baseline source" className="flex rounded-lg border bg-muted p-0.5 text-sm">
              {(["manual", "market"] as const).map((s) => (
                <button key={s} role="radio" type="button" aria-checked={r.source === s} disabled={s === "market" && !r.active}
                  onClick={() => r.source !== s && toggle(r, s)}
                  className={cn("flex-1 rounded-md px-3 py-1 disabled:opacity-50", r.source === s ? "bg-white shadow-sm" : "text-muted-foreground")}>
                  {s === "manual" ? "Manual" : `Market${r.active ? ` v${r.active.version}` : ""}`}
                </button>
              ))}
            </div>
            {!r.active && <p className="text-xs text-muted-foreground">Market is available once a version is published.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Postings</CardTitle>
            <CardDescription>{r.postings} posting{r.postings === 1 ? "" : "s"} from {r.companies} compan{r.companies === 1 ? "y" : "ies"}
              {Object.keys(r.levels ?? {}).length > 0 && ` (${Object.entries(r.levels).map(([l, n]) => `${n} ${l}`).join(", ")})`}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy !== null}
              title={r.configured.fetchable ? undefined : "No public job boards configured yet"}
              onClick={() => run("fetch", () => post<FetchResult>(`/market/${r.key}/fetch`).then((o) => setFetched({ role: r.key, ...o })))}>
              {busy === "fetch" ? <Loader2 className="animate-spin" /> : <Download />} Fetch now</Button>
            <AddPosting role={r} onAdded={load} />
            {fetched?.role === r.key && (
              <p role="status" aria-label="Fetch result" className={cn("w-full rounded-md px-2.5 py-1.5 text-xs",
                fetched.errors.length ? "bg-red-50 text-red-800" : fetched.boards ? "bg-emerald-50 text-emerald-900" : "bg-amber-50 text-amber-900")}>
                {fetched.boards === 0 && !fetched.errors.length
                  ? "No company has a public job board yet. Use Add postings."
                  : `${fetched.new} new posting${fetched.new === 1 ? "" : "s"}, ${fetched.seen - fetched.new} already known, ${fetched.closed} no longer listed.`}
                {fetched.errors.map((e) => <span key={e} className="block">{e}</span>)}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recompute</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy !== null} onClick={() => run("rc", async () => {
              const v = await post<MarketVersion & { left_out_note?: string | null }>(`/market/${r.key}/recompute`, {});
              if (v.left_out_note) toast.warning(`Draft ready, ${v.basis.charAt(0).toLowerCase()}${v.basis.slice(1)}. ${v.left_out_note}`, { duration: 15000 });
              else toast.success("Draft version ready");
            })}>
              {busy === "rc" ? <Loader2 className="animate-spin" /> : <RefreshCw />} Recompute</Button>
          </CardContent>
        </Card>
      </div>

      <Versions role={r.key} key={`${r.key}-${r.latest?.id}`} />
      <Postings role={r.key} key={`p-${r.key}-${r.postings}`} />
      <Suggestions />

      <Dialog open={!!switching} onOpenChange={(o) => !o && setSwitching(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Switch the baseline source?</DialogTitle>
            <DialogDescription>{switching?.candidates} candidate{switching?.candidates === 1 ? " is" : "s are"} re-scored the next time they are scored.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSwitching(null)}>Cancel</Button>
            <Button onClick={() => switching && toggle(data.roles.find((x) => x.key === switching.role)!, switching.to as "manual" | "market", true)}>Switch</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function VersionRow({ v }: { v: Omit<MarketVersion, "content"> }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-2">
      <Link href={`/admin/market/versions/${v.id}`} className="font-medium hover:underline">Version {v.version}</Link>
      <Badge variant={v.status === "published" ? "default" : "secondary"} className="capitalize">{v.status === "published" ? "active" : v.status}</Badge>
      <span className="text-muted-foreground">{v.basis} · {v.window_start} → {v.window_end}</span>
      <span className="ml-auto text-xs text-muted-foreground">{new Date(v.created_at).toLocaleString()}</span>
      {v.left_out_note && <p className="basis-full text-xs text-amber-800">{v.left_out_note}</p>}
    </li>
  );
}

/** The Active version and the latest draft; everything older under a collapsed History. */
function Versions({ role }: { role: string }) {
  const [vs, setVs] = useState<Omit<MarketVersion, "content">[] | null>(null);
  useEffect(() => { get<Omit<MarketVersion, "content">[]>(`/market/${role}/versions`).then(setVs).catch(() => setVs([])); }, [role]);
  const sorted = [...(vs ?? [])].sort((a, b) => b.version - a.version);
  const activeV = sorted.find((v) => v.status === "published");
  const draft = sorted.find((v) => v.status === "draft");
  const current = [draft, activeV].filter(Boolean) as Omit<MarketVersion, "content">[];
  const history = sorted.filter((v) => !current.includes(v));
  return (
    <section aria-label="Versions" className="rounded-xl border bg-white p-4">
      <h2 className="mb-2 font-semibold">Versions</h2>
      {!vs ? <Skeleton className="h-16" /> : vs.length === 0 ? <p className="text-sm text-muted-foreground">No versions yet.</p> : (
        <>
          <ul className="divide-y text-sm">{current.map((v) => <VersionRow key={v.id} v={v} />)}</ul>
          {history.length > 0 && (
            <details className="mt-2 text-sm" data-testid="version-history">
              <summary className="cursor-pointer py-1 text-muted-foreground">History ({history.length})</summary>
              <ul className="divide-y">{history.map((v) => <VersionRow key={v.id} v={v} />)}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}

function Postings({ role }: { role: string }) {
  const [rows, setRows] = useState<MarketPosting[] | null>(null);
  useEffect(() => { get<MarketPosting[]>(`/market/${role}/postings`).then(setRows).catch(() => setRows([])); }, [role]);
  if (!rows?.length) return null;
  return (
    <section aria-label="Postings in the window" className="rounded-xl border bg-white p-4">
      <h2 className="mb-2 font-semibold">Postings in the window ({rows.length})</h2>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Company</th><th>Tier</th><th>Title</th>
          <th>Level</th><th>Last seen</th></tr></thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id} className="border-t">
              <td className="py-1.5">{p.company}</td><td className="capitalize">{p.tier}</td>
              <td>{p.source_url ? <ExtLink url={p.source_url}>{p.title}</ExtLink> : p.title}</td>
              <td className="capitalize">{p.level ?? "—"}</td>
              <td className="text-muted-foreground">{new Date(p.last_seen).toLocaleDateString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Suggestions() {
  const [data, setData] = useState<{ suggestions: Suggestion[]; canonical: string[] } | null>(null);
  const [pick, setPick] = useState<Record<string, string>>({});
  const load = useCallback(() => { get<{ suggestions: Suggestion[]; canonical: string[] }>("/market/suggestions").then(setData).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);
  if (!data?.suggestions.length) return null;
  async function decide(s: Suggestion, approve: boolean) {
    try { await post(`/market/suggestions/${s.id}`, { approve, canonical: pick[s.id] || s.canonical }); load(); }
    catch (e) { toast.error((e as Error).message); }
  }
  return (
    <section aria-label="Name suggestions" className="rounded-xl border bg-white p-4">
      <h2 className="mb-2 font-semibold">New names</h2>

      <ul className="divide-y text-sm">
        {data.suggestions.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="font-medium">{s.name}</span><span className="text-muted-foreground">→</span>
            <select aria-label={`Merge ${s.name} into`} value={pick[s.id] ?? s.canonical ?? ""} onChange={(e) => setPick({ ...pick, [s.id]: e.target.value })}
              className="h-8 rounded-md border border-field-border bg-field px-2 text-sm">
              <option value="">Keep separate</option>
              {data.canonical.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <div className="ml-auto flex gap-1">
              <Button size="xs" disabled={!(pick[s.id] ?? s.canonical)} onClick={() => decide(s, true)}><Check /> Merge</Button>
              <Button size="xs" variant="outline" onClick={() => decide(s, false)}><X /> Reject</Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

type FromUrl = { source: string; title: string; company: string; text: string; posted_at: string | null; url: string;
  tier: string | null; title_matches: boolean; role_title: string };
const SOURCE_LABEL: Record<string, string> = { greenhouse: "the Greenhouse job board", lever: "the Lever job board",
  ashby: "the Ashby job board", "schema.org": "the page's job data", page: "the page text" };

function AddPosting({ role, onAdded }: { role: MarketRole; onAdded: () => void }) {
  const empty = { company: "", tier: "mid", title: role.title, url: "", posted_at: "", text: "" };
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(empty);
  const [file, setFile] = useState<File | null>(null);
  const maxBytes = useUploadLimit();
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [read, setRead] = useState<{ ok: boolean; message: string } | null>(null);
  const [titleOk, setTitleOk] = useState(true);
  const looksLikeUrl = (u: string) => /^https?:\/\/\S+\.\S+/i.test(u.trim());

  async function fillFromUrl(url = f.url) {
    if (!looksLikeUrl(url)) { setRead({ ok: false, message: "Enter a full URL starting with https://." }); return; }
    setReading(true); setRead(null);
    try {
      const d = await post<FromUrl>(`/market/${role.key}/postings/from-url`, { url: url.trim() });
      setF((cur) => ({ ...cur, url: url.trim(), company: d.company || cur.company, title: d.title || cur.title,
        posted_at: d.posted_at ?? cur.posted_at, text: d.text, tier: d.tier ?? cur.tier }));
      setFile(null);
      setTitleOk(d.title_matches);
      setRead({ ok: true, message: `Filled from ${SOURCE_LABEL[d.source] ?? "the page"}: ${d.text.length.toLocaleString()} characters. Check the fields, then add.` });
    } catch (e) {
      setRead({ ok: false, message: (e as Error).message });
    } finally { setReading(false); }
  }

  async function submit() {
    setBusy(true);
    const form = new FormData();
    Object.entries(f).forEach(([k, v]) => v && form.append(k, v));
    if (file) form.append("file", file);
    try {
      const out = await post<{ new: boolean }>(`/market/${role.key}/postings`, form);
      toast.success(out.new ? "Posting added" : "Already known: last seen updated");
      setOpen(false); setF(empty); setFile(null); setRead(null); onAdded();
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  }
  const missing = !f.company.trim() ? "Add the company." : !file && f.text.trim().length < 200
    ? "Add the posting text (at least 200 characters), a URL to fill it from, or a file." : null;
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}><Plus /> Add postings</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Add a posting · {role.title}</DialogTitle>
            <DialogDescription>Paste the posting&apos;s link to fill everything in, then check and add.</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="mp-url">Posting URL</Label>
            <div className="flex gap-2">
              <Input id="mp-url" value={f.url} placeholder="https://job-boards.greenhouse.io/company/jobs/123"
                onChange={(e) => setF({ ...f, url: e.target.value })}
                onPaste={(e) => { const u = e.clipboardData.getData("text"); if (looksLikeUrl(u)) { e.preventDefault(); setF({ ...f, url: u.trim() }); fillFromUrl(u); } }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fillFromUrl(); } }} />
              <Button type="button" variant="outline" className="shrink-0" disabled={reading || !f.url.trim()} onClick={() => fillFromUrl()}>
                {reading ? <><Loader2 className="animate-spin" /> Reading…</> : <><Download /> Fill from URL</>}
              </Button>
            </div>
            {read && <p role="status" className={cn("text-xs", read.ok ? "text-emerald-800" : "text-amber-800")}>{read.message}</p>}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1"><Label htmlFor="mp-company">Company</Label><Input id="mp-company" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="mp-tier">Tier</Label>
              <select id="mp-tier" value={f.tier} onChange={(e) => setF({ ...f, tier: e.target.value })} className="h-9 w-full rounded-lg border border-field-border bg-field px-2 text-sm">
                <option value="large">Large tech</option><option value="mid">Mid-size</option><option value="startup">Small startup</option></select></div>
            <div className="space-y-1"><Label htmlFor="mp-title">Job title</Label>
              <Input id="mp-title" value={f.title} onChange={(e) => { setF({ ...f, title: e.target.value }); setTitleOk(true); }} />
              {!titleOk && <p className="text-xs text-amber-800">This title doesn&apos;t look like a {role.title} role, so it may be refused when you add it.</p>}</div>
            <div className="space-y-1"><Label htmlFor="mp-date">Posted date</Label><Input id="mp-date" type="date" value={f.posted_at} onChange={(e) => setF({ ...f, posted_at: e.target.value })} /></div>
          </div>
          <div className="space-y-1">
            <div className="flex items-baseline justify-between"><Label htmlFor="mp-text">Posting text</Label>
              <span className="text-xs tabular-nums text-muted-foreground">{f.text.length.toLocaleString()} characters</span></div>
            <Textarea id="mp-text" value={f.text} disabled={!!file} placeholder="Filled from the URL, or paste the posting here."
              onChange={(e) => setF({ ...f, text: e.target.value })} className="h-48 min-h-48 [field-sizing:fixed] resize-y" />
          </div>
          <details className="group rounded-lg border px-3 py-2" open={!!file}>
            <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">Or upload a file instead</summary>
            <div className="pt-2"><FileDrop id="mp-file" label="File" what="posting" accept={[".pdf", ".docx"]}
              typesLabel="PDF or DOCX" file={file} onFile={setFile} maxBytes={maxBytes} /></div>
          </details>
          <DialogFooter className="items-center">
            {missing && <span className="mr-auto text-xs text-muted-foreground">{missing}</span>}
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button disabled={busy || !!missing} onClick={submit}>Add posting</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
