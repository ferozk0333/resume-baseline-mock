"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { BaselineSwitch } from "@/components/app/score/baseline-switch";
import { useRouter } from "next/navigation";
import { Loader2, RefreshCw, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ProgressPanel } from "@/components/app/progress-panel";
import { SubcategoryRow } from "@/components/app/score/subcategory-row";
import { WeightsDrawer } from "@/components/app/score/weights-drawer";
import { useTask } from "@/hooks/use-task";
import { DIMENSIONS, get, post, titleCase, type Baseline, type Benchmarks, type Pct, type RunView, type Weights } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

/** The Score page (specs/06), also shown inside My scores (`onOpenPlan` switches to the Plan tab there). */
export function ScoreView({ id, onOpenPlan, onRescored, technical = false }: { id: string; onOpenPlan?: () => void;
  onRescored?: (runId: string) => void; technical?: boolean }) {
  const router = useRouter();
  const [view, setView] = useState<RunView | null>(null);
  const [weights, setWeights] = useState<Weights | null>(null);       // what the sliders show; scores follow
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [bench, setBench] = useState<Benchmarks | null>(null);
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
    get<Benchmarks>(`/runs/${id}/benchmarks`).then(setBench).catch(() => setBench(null));
  }, [id]);
  // A run still being judged: poll until it is done (subcategories fill in; nothing is an error yet).
  const running = view?.run.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      get<RunView>(`/runs/${id}`).then((v) => {
        setView(v);
        if (v.run.status !== "running") get<Benchmarks>(`/runs/${id}/benchmarks`).then(setBench).catch(() => {});
      }).catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, [running, id]);
  const pct = bench?.percentiles;
  // Your score, the median for this role and the percentile, on one 0–100 scale (specs/13 §5). No ratio.
  const compare = (p: Pct | undefined, scale: number, dim?: string) => {
    if (!pct) return null;
    if (!pct.shown) return <p className="mt-1 text-[11px] leading-tight text-muted-foreground">{pct.reason}</p>;
    if (!p) return null;
    const you = p.value * scale, median = p.median * scale;
    const gap = dim ? view?.gaps.find((g) => g.dimension === dim) : undefined;
    return (
      <div className="mt-2 space-y-1">
        <MedianBar you={you} median={median} />
        <p className="text-[11px] leading-tight text-muted-foreground">
          Median for {bench?.baseline_title}: <span className="font-medium text-foreground">{Math.round(median)}</span>
          {" · "}you&apos;re higher than <span className="font-medium text-foreground">{p.percentile}%</span> of {p.n} other candidates
        </p>
        {dim && you < median && (
          onOpenPlan ? <button type="button" onClick={onOpenPlan} className="block text-left text-[11px] leading-tight font-medium text-amber-800 hover:underline">
            Below the median for this role — see what higher scorers have
          </button> : <Link href={`/runs/${id}/plan${gap ? `#gap-${gap.subcategory}` : ""}`}
            className="block text-[11px] leading-tight font-medium text-amber-800 hover:underline">
            Below the median for this role — see what higher scorers have
          </Link>
        )}
      </div>
    );
  };

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
    if (onRescored) { setRejudgeTask(null); onRescored(t.result.run_id); } else router.push(`/runs/${t.result.run_id}`);
  });

  if (rejudgeTask) return <ProgressPanel title="Checking everything again" task={rejudge} />;
  if (!view || !weights) return <Skeleton className="h-96" />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          {onOpenPlan ? <h2 className="text-lg font-semibold tracking-tight">Score</h2>
            : <h1 className="text-2xl font-semibold tracking-tight">Score</h1>}
          <p className="text-sm text-muted-foreground">Against {baseline?.title ?? "…"} ({baseline?.level ?? ""})
            {pct?.demo_pool && <Badge variant="outline" className="ml-2 border-amber-300 bg-amber-50 text-amber-800">demo pool</Badge>}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setDrawer(true)}><SlidersHorizontal /> Weights</Button>
          <Button variant="outline" onClick={() => setConfirm(true)}><RefreshCw /> Check again</Button>
          {onOpenPlan ? <Button onClick={onOpenPlan}>Gaps and plan</Button>
            : <Link href={`/runs/${id}/plan`} className={buttonVariants()}>Gaps and plan</Link>}
        </div>
      </div>
      <BaselineSwitch runId={id} />

      {running && (
        <div role="status" className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
          <Loader2 className="size-4 animate-spin" />
          Still scoring. Scores below are partial and this page updates by itself.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Final score</CardTitle></CardHeader>
          <CardContent>
            <div className="text-4xl font-semibold tabular-nums text-primary" data-testid="final-score">
              {view.final === null ? "—" : view.final.toFixed(1)}
            </div>
            <div className="text-xs text-muted-foreground">out of 100</div>
            {compare(pct?.final, 1)}
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
              {compare(pct?.shown ? pct.dimensions?.[d] : undefined, 100, d)}
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
            {view.subcategories.filter((s) => s.dimension === d).map((s) => <SubcategoryRow key={s.key} sub={s} technical={technical} />)}
          </div>
        </Card>
      ))}

      <WeightsDrawer open={drawer} onOpenChange={setDrawer} weights={weights} labels={labels} onChange={rescore} />

      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Check everything again?</DialogTitle>
            <DialogDescription>
              This takes a few minutes.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button onClick={async () => {
              setConfirm(false);
              try { setRejudgeTask((await post<{ task_id: string }>(`/runs/${id}/rejudge`)).task_id); }
              catch (e) { toast.error((e as Error).message); }
            }}>Check again</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function MedianBar({ you, median }: { you: number; median: number }) {
  const clamp = (x: number) => Math.min(100, Math.max(0, x));
  return (
    <div className="relative h-2 rounded-full bg-muted" role="img"
      aria-label={`Your score ${Math.round(you)}, median ${Math.round(median)}, on a 0 to 100 scale`}>
      <div className="absolute inset-y-0 left-0 rounded-full bg-primary/25" style={{ width: `${clamp(you)}%` }} />
      <div className="absolute -top-0.5 h-3 w-px bg-foreground/70" style={{ left: `${clamp(median)}%` }} title={`Median ${Math.round(median)}`} />
      <div className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-primary shadow"
        style={{ left: `${clamp(you)}%` }} title={`You ${Math.round(you)}`} />
    </div>
  );
}
