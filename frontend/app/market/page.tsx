"use client";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadError } from "@/components/app/candidate/load-error";
import { get, type MarketView } from "@/lib/api";
import { cn } from "@/lib/utils";

type RoleRow = { role: string; title: string; available: boolean };

/** Candidate side (read-only): what entry-level postings ask for, from the active Market version. Counts only. */
export default function MarketAsks() {
  const [roles, setRoles] = useState<RoleRow[] | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [view, setView] = useState<MarketView | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<RoleRow[]>("/market-view").then((r) => { setRoles(r); setRole((r.find((x) => x.available) ?? r[0])?.role ?? null); })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!role) return;
    get<MarketView>(`/market-view/${role}`).then(setView).catch(() => setView(null));
  }, [role]);
  if (error) return <LoadError what="Market data" error={error} onRetry={() => window.location.reload()} />;
  if (!roles) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">What the market asks for</h1>
        <p className="text-sm text-muted-foreground">From recent entry-level job postings: how many companies list each skill or tool, and whether it is usually required.</p>
      </div>
      <div role="tablist" aria-label="Role" className="flex w-fit max-w-full flex-wrap rounded-lg border bg-muted p-0.5 text-sm">
        {roles.map((r) => (
          <button key={r.role} role="tab" type="button" aria-selected={role === r.role} onClick={() => { setView(undefined); setRole(r.role); }}
            className={cn("rounded-md px-3 py-1", role === r.role ? "bg-white shadow-sm" : "text-muted-foreground")}>{r.title}</button>
        ))}
      </div>
      {view === undefined ? <Skeleton className="h-64" /> : view === null ? (
        <p className="rounded-xl border border-dashed bg-white px-4 py-8 text-center text-sm text-muted-foreground">No market data for this role yet.</p>
      ) : (
        <section aria-label="Market criteria" className="space-y-3 rounded-xl border bg-white p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>{view.postings} postings from {view.companies} companies · {view.window[0]} to {view.window[1]} · version {view.version}</span>
            {view.test && <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">Test version – insufficient data</Badge>}
          </div>
          <p className="text-sm">Typical minimum experience: <b>{view.required_years ?? "—"} year{view.required_years === 1 ? "" : "s"}</b> · Typical reach of the work: <b>{view.expected_reach ?? "—"}</b></p>
          <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm [&_td]:px-2 [&_th]:px-2">
            <thead className="text-left text-xs text-muted-foreground">
              <tr><th className="py-1">Skill or tool</th><th>Usually</th><th className="w-1/3">Companies listing it</th><th className="text-right">Postings</th></tr>
            </thead>
            <tbody>
              {view.criteria.map((c) => (
                <tr key={c.name} className="border-t">
                  <td className="py-1.5 font-medium">{c.name} <span className="text-xs font-normal text-muted-foreground">{c.kind}</span></td>
                  <td><span className={cn("rounded border px-1.5 py-0.5 text-xs", c.category === "required" ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-sky-200 bg-sky-50 text-sky-800")}>{c.category}</span></td>
                  <td>
                    <div className="flex items-center gap-2">
                      <div className="h-2 flex-1 rounded-full bg-muted"><div className="h-2 rounded-full bg-primary" style={{ width: `${Math.round(c.company_share * 100)}%` }} /></div>
                      <span className="w-24 text-xs tabular-nums text-muted-foreground">{c.companies} of {view.companies} ({Math.round(c.company_share * 100)}%)</span>
                    </div>
                  </td>
                  <td className="text-right tabular-nums">{c.postings}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </section>
      )}
    </div>
  );
}
