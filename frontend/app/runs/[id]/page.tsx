"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { RefreshCw, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ProgressPanel } from "@/components/app/progress-panel";
import { SubcategoryRow } from "@/components/app/score/subcategory-row";
import { WeightsDrawer } from "@/components/app/score/weights-drawer";
import { useTask } from "@/hooks/use-task";
import { DIMENSIONS, get, post, titleCase, type Baseline, type RunView, type Weights } from "@/lib/api";

export default function ScorePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><ScorePageInner /></Suspense>;
}

function ScorePageInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [view, setView] = useState<RunView | null>(null);
  const [weights, setWeights] = useState<Weights | null>(null);       // what the sliders show; scores follow
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [rejudgeTask, setRejudgeTask] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    get<RunView>(`/runs/${id}`).then((v) => {
      setView(v);
      setWeights(v.weights);
      get<Baseline>(`/baselines/${v.run.baseline_id}`).then(setBaseline).catch(() => {});
    }).catch((e) => toast.error(e.message));
    get<{ labels: Record<string, string> }>("/config/weights").then((d) => setLabels(d.labels)).catch(() => {});
  }, [id]);

  const rescore = useCallback((next: Weights) => {
    setWeights(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {           // re-aggregates saved scores: no model calls
      post<RunView>(`/runs/${id}/rescore`, { weights: next }).then(setView).catch((e) => toast.error(e.message));
    }, 150);
  }, [id]);

  const rejudge = useTask(rejudgeTask, async (status, last) => {
    if (status === "error") { toast.error(last.message); setRejudgeTask(null); return; }
    const t = await get<{ result: { run_id: string } }>(`/tasks/${rejudgeTask}`);
    router.push(`/runs/${t.result.run_id}`);
  });

  if (rejudgeTask) return <ProgressPanel title="Judging again and scoring" task={rejudge} />;
  if (!view || !weights) return <Skeleton className="h-96" />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Score</h1>
          <p className="text-sm text-muted-foreground">Against {baseline?.title ?? "…"} ({baseline?.level ?? ""})</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setDrawer(true)}><SlidersHorizontal /> Weights</Button>
          <Button variant="outline" onClick={() => setConfirm(true)}><RefreshCw /> Re-judge</Button>
          <Link href={`/runs/${id}/plan`} className={buttonVariants()}>Gaps and plan</Link>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Final score</CardTitle></CardHeader>
          <CardContent>
            <div className="text-4xl font-semibold tabular-nums text-primary" data-testid="final-score">
              {view.final === null ? "—" : view.final.toFixed(1)}
            </div>
            <div className="text-xs text-muted-foreground">out of 100</div>
          </CardContent>
        </Card>
        {DIMENSIONS.map((d) => (
          <Card key={d}>
            <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">{titleCase(d)}</CardTitle></CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold tabular-nums">
                {view.dimensions[d] === null ? "—" : Math.round((view.dimensions[d] ?? 0) * 100)}
              </div>
              <div className="text-xs text-muted-foreground">weight {Math.round(view.weights.dimensions[d])}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {DIMENSIONS.map((d) => (
        <Card key={d} className="gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="text-base">{titleCase(d)}</CardTitle>
          </CardHeader>
          <div>
            {view.subcategories.filter((s) => s.dimension === d).map((s) => <SubcategoryRow key={s.key} sub={s} />)}
          </div>
        </Card>
      ))}

      <WeightsDrawer open={drawer} onOpenChange={setDrawer} weights={weights} labels={labels} onChange={rescore} />

      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Judge everything again?</DialogTitle>
            <DialogDescription>
              Saved judgments for this role are cleared and the models are asked again. This takes a few minutes and uses model calls.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button onClick={async () => {
              setConfirm(false);
              try { setRejudgeTask((await post<{ task_id: string }>(`/runs/${id}/rejudge`)).task_id); }
              catch (e) { toast.error((e as Error).message); }
            }}>Re-judge</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
