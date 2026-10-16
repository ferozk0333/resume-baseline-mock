"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Plus } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EeatTip, RoleScoreCard } from "@/components/app/candidate/score-card";
import { LoadError } from "@/components/app/candidate/load-error";
import { ProgressPanel } from "@/components/app/progress-panel";
import { PlanView } from "@/components/app/score/plan-view";
import { ScoreView } from "@/components/app/score/score-view";
import { useTask } from "@/hooks/use-task";
import { del, get, post, type OnboardingState, type ScoreCard, type Target } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";
import { cn } from "@/lib/utils";

export default function ScoresPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Scores /></Suspense>;
}

/** My scores (specs/17 §8): a tab per target role, each with the Score and Plan pages; add, remove, primary. */
function Scores() {
  const router = useRouter();
  const search = useSearchParams();
  const [cid, ready] = useCurrentCandidate();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [cards, setCards] = useState<ScoreCard[] | null>(null);
  const [addTask, setAddTask] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const [removing, setRemoving] = useState<Target | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tab = search.get("tab") === "plan" ? "plan" : "score";

  const load = useCallback(() => {
    if (!cid) return;
    get<OnboardingState>(`/candidates/${cid}/onboarding`).then(setState).catch((e) => setError(e.message));
    get<ScoreCard[]>(`/candidates/${cid}/baseline-scores`).then(setCards).catch((e) => setError(e.message));
  }, [cid]);
  useEffect(() => {
    if (!ready) return;
    if (!cid) router.replace("/onboarding"); else load();
  }, [ready, cid, router, load]);
  const added = useTask(addTask, (status, last) => {
    setAddTask(null);
    if (status === "error") toast.error(last.message);
    load();
  });

  const role = search.get("role") ?? cards?.[0]?.baseline_id;
  const card = cards?.find((c) => c.baseline_id === role) ?? cards?.[0];
  const nav = (r: string | undefined, t: "score" | "plan") =>
    router.replace(`/scores?${new URLSearchParams({ ...(r ? { role: r } : {}), ...(t === "plan" ? { tab: "plan" } : {}) })}`);

  if (error) return <LoadError what="Your scores" error={error} onRetry={() => { setError(null); load(); }} />;
  if (!ready || !cards || !state) return <Skeleton className="h-96" />;
  if (state.onboarding.step !== "done" && cards.every((c) => c.status !== "scored")) {
    return <Card className="max-w-xl"><CardContent className="space-y-3 py-6">
      <p className="text-sm">Your scores appear here once your profile is set up.</p>
      <Link href="/onboarding" className={buttonVariants()}>Continue setup</Link></CardContent></Card>;
  }
  const others = state.roles.filter((r) => !cards.some((c) => c.baseline_id === r.baseline_id));

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); toast.success(ok); load(); } catch (e) { toast.error((e as Error).message); }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My scores</h1>
          <p className="text-sm text-muted-foreground">Your baseline score per role, from <EeatTip />. Where you stand on average for the role, not for one job.</p>
        </div>
        <Link href="/market" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
          What the market asks for <ArrowRight className="size-4" /></Link>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Target roles" className="flex flex-wrap rounded-lg border bg-muted p-0.5 text-sm">
          {cards.map((c) => (
            <button key={c.baseline_id} role="tab" type="button" aria-selected={c.baseline_id === card?.baseline_id}
              onClick={() => nav(c.baseline_id, "score")}
              className={cn("flex items-center gap-1.5 rounded-md px-3 py-1", c.baseline_id === card?.baseline_id ? "bg-white shadow-sm" : "text-muted-foreground")}>
              {c.title}{c.is_primary && <Badge className="text-[10px]">Primary</Badge>}
            </button>
          ))}
        </div>
        {others.length > 0 && cards.length < 3 && (
          <div className="flex items-center gap-1.5">
            <label htmlFor="add-role" className="sr-only">Add a role</label>
            <select id="add-role" value={adding} onChange={(e) => setAdding(e.target.value)} disabled={!!addTask}
              className="h-8 rounded-lg border border-input bg-white px-2 text-sm">
              <option value="">Add a role…</option>
              {others.map((r) => <option key={r.baseline_id} value={r.baseline_id}>{r.title}</option>)}
            </select>
            <Button size="sm" variant="outline" disabled={!adding || !!addTask} onClick={async () => {
              try {
                const r = await post<{ targets: Target[]; task_id: string | null }>(`/candidates/${cid}/targets`, { baseline_id: adding });
                nav(adding, "score");
                setAdding("");
                if (r.task_id) setAddTask(r.task_id); else load();
              } catch (e) { toast.error((e as Error).message); }
            }}><Plus /> Add</Button>
          </div>
        )}
      </div>

      {addTask && <ProgressPanel title="Scoring your profile for the new role" task={added} />}

      {card && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {!card.is_primary && <Button size="sm" variant="outline"
              onClick={() => act(() => post(`/candidates/${cid}/targets/${card.baseline_id}/primary`), `${card.title} is now your primary role`)}>
              Make primary</Button>}
            {cards.length > 1 && <Button size="sm" variant="ghost" onClick={() => setRemoving(card)}>Remove role</Button>}
          </div>
          {card.status !== "scored" || !card.run_id ? <RoleScoreCard card={card} /> : (
            <div className="space-y-4">
              <div role="tablist" aria-label="Score or plan" className="flex w-fit rounded-lg border bg-muted p-0.5 text-sm">
                {(["score", "plan"] as const).map((t) => (
                  <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => nav(card.baseline_id, t)}
                    className={cn("rounded-md px-3 py-1", tab === t ? "bg-white shadow-sm" : "text-muted-foreground")}>
                    {t === "score" ? "Score" : "Gaps and plan"}</button>
                ))}
              </div>
              {tab === "score"
                ? <ScoreView key={card.run_id} id={card.run_id} onOpenPlan={() => nav(card.baseline_id, "plan")} onRescored={() => load()} />
                : <PlanView key={card.run_id} id={card.run_id} onBack={() => nav(card.baseline_id, "score")} />}
            </div>
          )}
        </>
      )}

      <Dialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {removing?.title}?</DialogTitle>
            <DialogDescription>It is no longer shown on Home or here. Your saved score stays, and adding the role back reuses it.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRemoving(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => {
              const r = removing!;
              setRemoving(null);
              act(() => del(`/candidates/${cid}/targets/${r.baseline_id}`), `${r.title} removed`).then(() => nav(undefined, "score"));
            }}>Remove</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
