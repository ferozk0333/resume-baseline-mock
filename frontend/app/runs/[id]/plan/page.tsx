"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
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


export default function PlanPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><PlanPageInner /></Suspense>;
}

function PlanPageInner() {
  const { id } = useParams<{ id: string }>();
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
  const generating = plan.status === "generating" || !!regenTask;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href={`/runs/${id}`} className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" /> Back to the score
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Gaps and improvement plan</h1>
        </div>
        <Button variant="outline" disabled={generating} onClick={async () => {
          try { setRegenTask((await post<{ task_id: string }>(`/runs/${id}/plan/regenerate`, { weights: run.weights })).task_id); }
          catch (e) { toast.error((e as Error).message); }
        }}><RefreshCw /> {plan.status === "none" ? "Generate plan" : "Regenerate plan"}</Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ranked gaps</CardTitle>
          <CardDescription>Points the final score could gain if the subcategory reached its maximum.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow><TableHead>Subcategory</TableHead><TableHead>Status</TableHead>
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
            <Loader2 className="size-5 animate-spin text-primary" /> {regen.message ?? "Writing your improvement plan…"}
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
                    {a.addresses.map((g) => <Badge key={g} variant="outline">{g.replaceAll("_", " ")}</Badge>)}
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
