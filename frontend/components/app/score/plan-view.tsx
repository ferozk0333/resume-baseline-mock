"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge } from "@/components/app/score/subcategory-row";
import { useTask } from "@/hooks/use-task";
import { get, post, type Benchmarks, type Plan, type RunView } from "@/lib/api";


/** Gaps and the improvement plan (specs/06). A plan left for later (specs/17 §3.1: only the primary role's plan is
 * written during onboarding) is prepared on first open. Inside My scores, `onBack` returns to the Score tab. */
export function PlanView({ id, onBack }: { id: string; onBack?: () => void }) {
  const [run, setRun] = useState<RunView | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [regenTask, setRegenTask] = useState<string | null>(null);
  const [bench, setBench] = useState<Benchmarks | null>(null);

  const load = useCallback(() => get<Plan>(`/runs/${id}/plan`).then(setPlan).catch((e) => toast.error(e.message)), [id]);
  useEffect(() => {
    get<RunView>(`/runs/${id}`).then(setRun).catch((e) => toast.error(e.message));
    get<Benchmarks>(`/runs/${id}/benchmarks`).then(setBench).catch(() => setBench(null));
    load();
  }, [id, load]);
  useEffect(() => {                                      // "#gap-…" from the Score page: scroll once the rows exist
    if (run && window.location.hash) document.getElementById(window.location.hash.slice(1))?.scrollIntoView({ block: "center" });
  }, [run]);

  useEffect(() => {                                      // first open of a deferred plan: prepare it now
    if (plan?.status !== "deferred" && plan?.status !== "none") return;
    if (run?.run.status !== "done") return;
    post<{ status: string }>(`/runs/${id}/plan`).then(() => load()).catch((e) => toast.error(e.message));
  }, [plan?.status, run?.run.status, id, load]);
  useEffect(() => {                                      // the plan is written after the score: poll until ready
    if (plan?.status !== "generating") return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [plan?.status, load]);

  const regen = useTask(regenTask, (status, last) => {
    setRegenTask(null);
    if (status === "error") toast.error(last.message);
    else { toast.success("New plan ready"); load(); }
  });

  if (!run || !plan) return <Skeleton className="h-96" />;
  const generating = plan.status === "generating" || plan.status === "deferred" || !!regenTask;
  // Why "What higher scorers have" lines are missing (same reasons as the Score page): too few candidates, or a
  // scripted test run.
  const pct = bench?.percentiles;
  const peerNote = !bench || !pct ? null : !pct.shown ? (pct.reason ?? "Not enough candidates for a comparison yet")
    : Object.values(bench.peer_signals.dimensions).every((x) => x.suppressed || x.signals.length === 0)
      ? `Not enough candidates for ${bench.baseline_title ?? "this role"} yet to compare groups.` : null;
  const label = (key: string) => run.subcategories.find((s) => s.key === key)?.label ?? key.replaceAll("_", " ");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          {onBack ? (
            <button type="button" onClick={onBack} className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
              <ArrowLeft className="size-4" /> Back to the score
            </button>
          ) : (
            <Link href={`/runs/${id}`} className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
              <ArrowLeft className="size-4" /> Back to the score
            </Link>
          )}
          {onBack ? <h2 className="text-lg font-semibold tracking-tight">Gaps and improvement plan</h2>
            : <h1 className="text-2xl font-semibold tracking-tight">Gaps and improvement plan</h1>}
        </div>
        <Button variant="outline" disabled={generating} onClick={async () => {
          try { setRegenTask((await post<{ task_id: string }>(`/runs/${id}/plan/regenerate`, { weights: run.weights })).task_id); }
          catch (e) { toast.error((e as Error).message); }
        }}><RefreshCw /> {plan.status === "none" ? "Generate plan" : "Regenerate plan"}</Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ranked gaps</CardTitle>
          <CardDescription>Points the final score could gain if this area reached its maximum.</CardDescription>
          {peerNote && <p className="text-xs text-muted-foreground" data-testid="peer-note">
            <span className="font-medium text-foreground">What higher scorers have: </span>{peerNote}</p>}
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow><TableHead>Area</TableHead><TableHead>Status</TableHead>
                <TableHead className="text-right">Score</TableHead><TableHead className="text-right">Potential</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {run.gaps.map((g) => (
                <TableRow key={g.subcategory} id={`gap-${g.subcategory}`} className="scroll-mt-20 target:bg-amber-50">
                  <TableCell className="font-medium whitespace-normal">
                    {g.label}
                    {(bench?.peer_signals.dimensions[g.dimension]?.signals ?? []).filter((s) => s.subcategory === g.subcategory).map((s) => (
                      <p key={s.feature} className="mt-1 max-w-md text-xs font-normal text-muted-foreground">
                        <span className="font-medium text-foreground">What higher scorers have: </span>{s.text}
                      </p>
                    ))}
                  </TableCell>
                  <TableCell><StatusBadge status={g.status} /></TableCell>
                  <TableCell className="text-right tabular-nums">{g.score.toFixed(2)}</TableCell>
                  <TableCell className="text-right tabular-nums">{g.gain_text}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {generating ? (
        <Card aria-live="polite">
          <CardContent className="flex items-center gap-3 py-8 text-sm text-muted-foreground">
            <Loader2 className="size-5 animate-spin text-primary" /> {regen.message ?? "Preparing your plan…"}
          </CardContent>
        </Card>
      ) : plan.status === "failed" ? (
        <Card><CardContent className="py-6 text-sm text-destructive">
          The plan could not be generated{plan.error ? `: ${plan.error}` : ""}. The ranked gaps above are still valid.
        </CardContent></Card>
      ) : plan.actions.length === 0 ? (
        <Card><CardContent className="py-6 text-sm text-muted-foreground">No plan yet.</CardContent></Card>
      ) : (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">What to do next</h2>
          <div className="grid gap-4 md:grid-cols-2">
            {plan.actions.map((a, i) => (
              <Card key={i}>
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant="secondary">{a.type_label}</Badge>
                    <span className="text-sm font-medium text-primary">{a.points_text}</span>
                  </div>
                  <CardTitle className="text-base">{a.title}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <p>{a.what_to_do}</p>
                  {a.must_show.length > 0 && (
                    <div><div className="font-medium">It must show</div>
                      <ul className="list-disc pl-5 text-muted-foreground">{a.must_show.map((m, j) => <li key={j}>{m}</li>)}</ul></div>
                  )}
                  <div className="flex flex-wrap gap-1.5">
                    {a.addresses.map((g) => <Badge key={g} variant="outline">{label(g)}</Badge>)}
                    {a.covers_criteria.map((c) => <Badge key={c} variant="outline" className="border-primary/30 text-primary">{c}</Badge>)}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {!!plan.quick_wins?.length && (
              <Card><CardHeader><CardTitle className="text-base">Quick wins</CardTitle></CardHeader>
                <CardContent><ul className="list-disc space-y-1 pl-5 text-sm">{plan.quick_wins.map((q, i) => <li key={i}>{q}</li>)}</ul></CardContent></Card>
            )}
            {!!plan.not_actionable?.length && (
              <Card><CardHeader><CardTitle className="text-base">Not actionable now</CardTitle>
                <CardDescription>Gaps a near-term action cannot close.</CardDescription></CardHeader>
                <CardContent><ul className="list-disc space-y-1 pl-5 text-sm">{plan.not_actionable.map((q, i) => <li key={i}>{q}</li>)}</ul></CardContent></Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
