"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { FileDrop } from "@/components/ui/file-drop";
import { Check, Download, FlaskConical, Loader2, Plus, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, get, post, put, useUploadLimit, type MarketPosting, type MarketRole, type MarketVersion, type Suggestion } from "@/lib/api";
import { cn } from "@/lib/utils";

type FetchResult = { boards: number; seen: number; new: number; excluded_titles: number; closed: number; errors: string[];
  skipped: number };
type Overview = { roles: MarketRole[]; allow_test_versions: boolean; thresholds: Record<string, number> };

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
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Market baselines</h1>
          <p className="text-sm text-muted-foreground">Baselines built from real job postings over the last {data.thresholds.WINDOW_DAYS} days.
            Nothing goes live until a person publishes it.</p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={data.allow_test_versions}
            onChange={(e) => run("test", () => put("/market/settings", { allow_test_versions: e.target.checked }))} />
          Allow test versions <span className="text-xs text-muted-foreground">(publish with fewer than {data.thresholds.MIN_COMPANIES} companies / {data.thresholds.MIN_POSTINGS} postings)</span>
        </label>
      </div>
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
            <CardDescription>The default for new {r.title} scores. Candidates can switch on their Score page; jobs created by recruiters keep their own baseline.</CardDescription>
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
            <CardDescription>{r.postings} in the window from {r.companies} companies
              {Object.keys(r.levels ?? {}).length > 0 && ` (${Object.entries(r.levels).map(([l, n]) => `${n} ${l}`).join(", ")}; only entry builds the baseline for now)`}. {r.configured.placeholders} of {r.configured.companies} configured
              companies are placeholders (config/market_sources.yaml).</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy !== null}
              title={r.configured.fetchable ? undefined : "No public job boards configured yet"}
              onClick={() => run("fetch", () => post<FetchResult>(`/market/${r.key}/fetch`).then((o) => setFetched({ role: r.key, ...o })))}>
              {busy === "fetch" ? <Loader2 className="animate-spin" /> : <Download />} Fetch now</Button>
            <AddPosting role={r} onAdded={load} />
            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => run("fx", () => post(`/market/${r.key}/fixtures`), "Test fixtures loaded")}>
              <FlaskConical /> Load test fixtures</Button>
            {fetched?.role === r.key && (
              <p role="status" aria-label="Fetch result" className={cn("w-full rounded-md px-2.5 py-1.5 text-xs",
                fetched.errors.length ? "bg-red-50 text-red-800" : fetched.boards ? "bg-emerald-50 text-emerald-900" : "bg-amber-50 text-amber-900")}>
                {fetched.boards === 0 && !fetched.errors.length
                  ? `Nothing to fetch: none of the ${r.configured.companies} companies has a public job board set yet (${fetched.skipped} skipped: placeholders or manual). Add the real companies in config/market_sources.yaml (source greenhouse, lever or ashby with its board id), or use Add postings.`
                  : `Read ${fetched.boards} board${fetched.boards === 1 ? "" : "s"}: ${fetched.new} new posting${fetched.new === 1 ? "" : "s"}, ${fetched.seen - fetched.new} already known, ${fetched.excluded_titles} other titles left out, ${fetched.closed} no longer listed, ${fetched.skipped} skipped.`}
                {fetched.errors.map((e) => <span key={e} className="block">{e}</span>)}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recompute</CardTitle>
            <CardDescription>A new draft version: new postings are read once (cached), then aggregated in code.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy !== null} onClick={() => run("rc", () => post(`/market/${r.key}/recompute`, { test: false }), "Draft version ready")}>
              {busy === "rc" ? <Loader2 className="animate-spin" /> : <RefreshCw />} Recompute</Button>
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => run("rct", () => post(`/market/${r.key}/recompute`, { test: true }), "Draft version ready (no model)")}>
              Recompute without a model</Button>
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
            <DialogDescription>Switching will re-score {switching?.candidates} candidate{switching?.candidates === 1 ? "" : "s"} (about {switching?.calls} model
              calls) the next time they are scored: judgments that depend on the baseline are redone.</DialogDescription>
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

function Versions({ role }: { role: string }) {
  const [vs, setVs] = useState<Omit<MarketVersion, "content">[] | null>(null);
  useEffect(() => { get<Omit<MarketVersion, "content">[]>(`/market/${role}/versions`).then(setVs).catch(() => setVs([])); }, [role]);
  return (
    <section aria-label="Versions" className="rounded-xl border bg-white p-4">
      <h2 className="mb-2 font-semibold">Versions</h2>
      {!vs ? <Skeleton className="h-16" /> : vs.length === 0 ? <p className="text-sm text-muted-foreground">No versions yet: add postings, then Recompute.</p> : (
        <ul className="divide-y text-sm">
          {vs.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-2 py-2">
              <Link href={`/admin/market/versions/${v.id}`} className="font-medium hover:underline">Version {v.version}</Link>
              <Badge variant={v.status === "published" ? "default" : "secondary"} className="capitalize">{v.status === "published" ? "active" : v.status}</Badge>
              {v.insufficient && <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">Insufficient data</Badge>}
              {v.test_version && <Badge variant="outline">test version</Badge>}
              <span className="text-muted-foreground">{v.postings} postings · {v.companies} companies · {v.window_start} → {v.window_end}</span>
              <span className="ml-auto text-xs text-muted-foreground">{new Date(v.created_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
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
        <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Company</th><th>Tier</th><th>Title</th><th>Source</th>
          <th>Level</th><th>Read</th><th>Last seen</th></tr></thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id} className="border-t">
              <td className="py-1.5">{p.company}</td><td className="capitalize">{p.tier}</td>
              <td>{p.source_url ? <a href={p.source_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{p.title}</a> : p.title}</td>
              <td>{p.source}</td><td>{p.level ?? "—"}{p.level_method && <span className="text-xs text-muted-foreground"> ({p.level_method})</span>}</td>
              <td>{p.extracted ? <Check className="size-4 text-emerald-600" /> : <span className="text-xs text-muted-foreground">on recompute</span>}</td>
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
      <h2 className="font-semibold">New names</h2>
      <p className="mb-2 text-sm text-muted-foreground">Names not in the reviewed synonym map. Approve a merge into an existing criterion, or reject to keep the name separate.</p>
      <ul className="divide-y text-sm">
        {data.suggestions.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="font-medium">{s.name}</span><span className="text-muted-foreground">→</span>
            <select aria-label={`Merge ${s.name} into`} value={pick[s.id] ?? s.canonical ?? ""} onChange={(e) => setPick({ ...pick, [s.id]: e.target.value })}
              className="h-8 rounded-md border border-field-border bg-field px-2 text-sm">
              <option value="">Keep separate</option>
              {data.canonical.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            {s.method && <Badge variant="outline" className="text-[10px]">proposed by {s.method}</Badge>}
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

function AddPosting({ role, onAdded }: { role: MarketRole; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ company: "", tier: "mid", title: role.title, url: "", posted_at: "", text: "" });
  const [file, setFile] = useState<File | null>(null);
  const maxBytes = useUploadLimit();
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    const form = new FormData();
    Object.entries(f).forEach(([k, v]) => v && form.append(k, v));
    if (file) form.append("file", file);
    try {
      const out = await post<{ new: boolean }>(`/market/${role.key}/postings`, form);
      toast.success(out.new ? "Posting added" : "Already known: last seen updated");
      setOpen(false); onAdded();
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}><Plus /> Add postings</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Add a posting · {role.title}</DialogTitle>
            <DialogDescription>For companies without a public job-board API. Paste the text or upload a PDF/DOCX.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1"><Label htmlFor="mp-company">Company</Label><Input id="mp-company" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="mp-tier">Tier</Label>
              <select id="mp-tier" value={f.tier} onChange={(e) => setF({ ...f, tier: e.target.value })} className="h-9 w-full rounded-lg border border-field-border bg-field px-2 text-sm">
                <option value="large">Large tech</option><option value="mid">Mid-size</option><option value="startup">Small startup</option></select></div>
            <div className="space-y-1"><Label htmlFor="mp-title">Job title</Label><Input id="mp-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="mp-date">Posted date</Label><Input id="mp-date" type="date" value={f.posted_at} onChange={(e) => setF({ ...f, posted_at: e.target.value })} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="mp-url">URL</Label><Input id="mp-url" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></div>
            <div className="sm:col-span-2"><FileDrop id="mp-file" label="File" what="posting" accept={[".pdf", ".docx"]}
              typesLabel="PDF or DOCX" file={file} onFile={setFile} maxBytes={maxBytes} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="mp-text">Or paste the text</Label>
              <Textarea id="mp-text" value={f.text} disabled={!!file} onChange={(e) => setF({ ...f, text: e.target.value })} className="min-h-32" /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button disabled={busy || !f.company || (!file && f.text.length < 200)} onClick={submit}>Add posting</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
