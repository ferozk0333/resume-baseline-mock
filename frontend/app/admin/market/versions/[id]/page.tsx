"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { post, get, REACH, type MarketVersion, type SideBySide } from "@/lib/api";
import { cn } from "@/lib/utils";

type View = MarketVersion & { diff: { active_version: number | null; changes: string[] }; side_by_side: SideBySide;
  role_title: string; can_publish: boolean };
const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`);
const CAT: Record<string, string> = { required: "bg-emerald-50 text-emerald-800 border-emerald-200",
  preferred: "bg-sky-50 text-sky-800 border-sky-200", dropped: "bg-slate-100 text-slate-500 border-slate-200" };

export default function Page() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Insights /></Suspense>;
}

/** Market insights for one version (specs/15 §8): shares by tier, categories, changes, diff, Manual vs Market. */
function Insights() {
  const { id } = useParams<{ id: string }>();
  const [v, setV] = useState<View | null>(null);
  const [showDropped, setShowDropped] = useState(false);
  const load = useCallback(() => { get<View>(`/market/versions/${id}`).then(setV).catch((e) => toast.error(e.message)); }, [id]);
  useEffect(() => { load(); }, [load]);
  async function act(what: "publish" | "discard") {
    try { await post(`/market/versions/${id}/${what}`); toast.success(what === "publish" ? "Published: now the active Market version" : "Discarded"); load(); }
    catch (e) { toast.error((e as Error).message); }
  }
  if (!v) return <Skeleton className="h-96" />;
  const c = v.content;
  const rows = c.criteria.filter((x) => showDropped || x.category !== "dropped");
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <Link href="/admin/market" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Market baselines</Link>
          <h1 className="text-2xl font-semibold tracking-tight">Market insights · {v.role_title} · version {v.version}</h1>
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Badge variant={v.status === "published" ? "default" : "secondary"} className="capitalize">{v.status === "published" ? "active" : v.status}</Badge>
            {v.basis} · {v.window_start} → {v.window_end} ·
            {" "}{Object.entries(c.tiers).map(([t, n]) => `${t} ${n.companies}/${n.postings}`).join(", ")} (companies/postings)
          </p>
        </div>
        {v.status === "draft" && (
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => act("discard")}>Discard</Button>
            <Button disabled={!v.can_publish} onClick={() => act("publish")}>Publish</Button>
          </div>
        )}
      </div>

      <section aria-label="Diff" className="rounded-xl border bg-white p-4 text-sm">
        <h2 className="mb-1 font-semibold">{v.status === "draft" ? "Against the active version" : "Against the version it replaced"}
          {v.diff.active_version ? ` (v${v.diff.active_version})` : " (none: first Market version)"}</h2>
        {v.diff.changes.length ? <ul className="list-disc pl-5">{v.diff.changes.map((x) => <li key={x}>{x}</li>)}</ul>
          : <p className="text-muted-foreground">No material change; publishing would keep the active version.</p>}
      </section>

      <section aria-label="Criteria" className="rounded-xl border bg-white p-4">
        <div className="mb-2 flex items-center gap-3">
          <h2 className="font-semibold">Criteria</h2>
          <label className="ml-auto flex items-center gap-1.5 text-sm"><input type="checkbox" checked={showDropped} onChange={(e) => setShowDropped(e.target.checked)} /> Show dropped</label>
        </div>
        <div className="overflow-x-auto">
          <table aria-label="Market criteria" className="w-full min-w-[64rem] text-sm [&_td]:px-2 [&_th]:px-2">
            <thead className="text-left text-xs text-muted-foreground">
              <tr><th className="py-1">Criterion</th><th className="text-right">Overall</th><th className="text-right">As required</th>
                <th className="text-right">Large</th><th className="text-right">Mid</th><th className="text-right">Startup</th>
                <th>Category</th><th>Change</th><th className="text-right">Postings</th><th className="text-right">Companies</th><th>Example</th></tr>
            </thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.name} className="border-t align-top">
                  <td className="py-1.5 font-medium">{x.name}<span className="ml-1 text-xs text-muted-foreground">{x.kind}</span>
                    {x.variants.length > 0 && <div className="text-xs font-normal text-muted-foreground">also: {x.variants.join(", ")}</div>}</td>
                  <td className="text-right tabular-nums">{pct(x.share)}</td><td className="text-right tabular-nums">{pct(x.required_share)}</td>
                  {["large", "mid", "startup"].map((t) => <td key={t} className="text-right tabular-nums text-muted-foreground">{pct(x.by_tier[t])}</td>)}
                  <td><span className={cn("rounded border px-1.5 py-0.5 text-xs capitalize", CAT[x.category])}>{x.category}</span></td>
                  <td className="text-xs">{x.change ?? ""}{x.share_delta ? ` (${x.share_delta > 0 ? "+" : ""}${Math.round(x.share_delta * 100)} pts)` : ""}</td>
                  <td className="text-right tabular-nums">{x.postings}</td><td className="text-right tabular-nums">{x.companies}</td>
                  <td className="max-w-72 text-xs text-muted-foreground">{x.quotes[0] ? `“${x.quotes[0]}”` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-label="Expectations" className="space-y-2 rounded-xl border bg-white p-4 text-sm">
          <h2 className="font-semibold">Years, reach, scope and complexity</h2>
          <p><span className="text-muted-foreground">Minimum years (median):</span> {c.required_years ?? "—"} · <span className="text-muted-foreground">Reach (most common):</span> {c.expected_reach ? REACH[c.expected_reach] : "—"}</p>
          <p><span className="text-muted-foreground">Scope:</span> {c.scope_expectation ?? "—"}</p>
          <p><span className="text-muted-foreground">Complexity:</span> {c.complexity_criteria ?? "—"}</p>
        </section>
        <SideBySidePanel s={v.side_by_side} />
      </div>
    </div>
  );
}

function SideBySidePanel({ s }: { s: SideBySide }) {
  return (
    <section aria-label="Manual vs Market" className="rounded-xl border bg-white p-4 text-sm">
      <h2 className="mb-2 font-semibold">Manual vs Market {s.market ? `(v${s.market.version})` : ""}</h2>
      <table className="w-full [&_td]:px-1.5 [&_th]:px-1.5">
        <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Criterion</th><th>Manual</th><th>Market</th><th className="text-right">Share</th></tr></thead>
        <tbody>
          <tr className="border-t"><td className="py-1">Minimum years</td><td>{s.manual.required_years}</td><td>{s.market?.required_years ?? "—"}</td><td /></tr>
          <tr className="border-t"><td className="py-1">Reach</td><td>{s.manual.expected_reach}</td><td>{s.market?.expected_reach ?? "—"}</td><td /></tr>
          {s.rows.map((r) => (
            <tr key={r.name} className={cn("border-t", r.manual !== r.market && "bg-amber-50/50")}>
              <td className="py-1">{r.name}</td><td className="capitalize">{r.manual ?? "—"}</td><td className="capitalize">{r.market ?? "—"}</td>
              <td className="text-right tabular-nums">{pct(r.share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
