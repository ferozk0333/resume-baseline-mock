"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadError } from "@/components/app/candidate/load-error";
import { PercentileLine } from "@/components/app/candidate/score-card";
import { JobsBrowser } from "@/components/app/candidate/jobs-browser";
import { ADD_PROOF_HINT, markedDoneToast, openProof, PROOF_SECTION, THEN_CONFIRM } from "@/components/app/score/add-proof";
import { ApiError, get, post, STEP_LABELS, type CardAction, type OnboardingState, type ScoreCard } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";

export default function HomePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Home /></Suspense>;
}

/** Candidate Home (specs/17 §4): the baseline score strip with one next best action, "Finish your profile", and the
 * ranked "Jobs for you" list with the job detail on the right. */
function Home() {
  const router = useRouter();
  const [cid, ready, setCid] = useCurrentCandidate();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [cards, setCards] = useState<ScoreCard[] | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [cardsError, setCardsError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!cid) { router.replace("/onboarding"); return; }           // first run
    get<OnboardingState>(`/candidates/${cid}/onboarding`).then((s) => {
      setState(s);
      if (s.onboarding.step === "done") get<ScoreCard[]>(`/candidates/${cid}/baseline-scores`).then(setCards)
        .catch((e) => setCardsError(e.message));
    }).catch((e) => {
      if (e instanceof ApiError && e.status === 404) setCid(null); else toast.error(e.message);
    });
  }, [ready, cid, router, setCid]);

  if (!ready || !cid || !state) return <Skeleton className="h-96" />;
  const step = state.onboarding.step;
  if (step !== "done") {
    const at = STEP_LABELS.findIndex((s) => s.key === step);
    return (
      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>Finish setting up your profile</CardTitle>
          <CardDescription>Step {at + 1} of {STEP_LABELS.length}: {STEP_LABELS[at].label}</CardDescription>
        </CardHeader>
        <CardContent><Link href="/onboarding" className={buttonVariants()}>Continue</Link></CardContent>
      </Card>
    );
  }
  const card = cards?.find((c) => c.baseline_id === role) ?? cards?.[0];
  const next = card?.next_action ?? null;
  const lastDone = card?.done_actions.at(-1) ?? null;
  const addProof = (a: CardAction) => { if (cid) void openProof(cid, a.type, router.push); };
  async function markDone(a: CardAction, done: boolean) {
    if (!card) return;
    try {
      const updated = await post<ScoreCard>(`/candidates/${cid}/plan-done`, { baseline_id: card.baseline_id, title: a.title, done });
      setCards((cs) => (cs ?? []).map((c) => (c.baseline_id === updated.baseline_id ? updated : c)));
      if (done) markedDoneToast(a.type, () => addProof(a), () => { void markDone(a, false); });
    } catch (e) { toast.error((e as Error).message); }
  }
  const finish = !state.details.saved_at && !state.details.dismissed_at && !dismissed;

  return (
    <div className="space-y-6">
      <h1 className="sr-only">Home</h1>
      {cardsError ? <LoadError what="Your baseline score" error={cardsError} onRetry={() => {
        setCardsError(null);
        get<ScoreCard[]>(`/candidates/${cid}/baseline-scores`).then(setCards).catch((e) => setCardsError(e.message));
      }} /> : !cards ? <Skeleton className="h-28" /> : card && (
        <Card>
          <CardContent className="grid gap-5 md:grid-cols-2">
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor="strip-role" className="sr-only">Role</label>
                {cards.length > 1 ? (
                  <select id="strip-role" value={card.baseline_id} onChange={(e) => setRole(e.target.value)}
                    className="h-8 rounded-lg border border-field-border bg-field px-2 text-sm font-medium">
                    {cards.map((c) => <option key={c.baseline_id} value={c.baseline_id}>{c.title}{c.is_primary ? " (primary)" : ""}</option>)}
                  </select>
                ) : <span className="text-sm font-medium">{card.title}</span>}
                <span className="text-sm text-muted-foreground">baseline score</span>
              </div>
              <div className="text-4xl font-semibold tabular-nums text-primary">{card.final?.toFixed(0) ?? "—"}
                <span className="ml-1 text-sm font-normal text-muted-foreground">/ 100</span></div>
              <PercentileLine card={card} />
              <Link href={`/scores?role=${card.baseline_id}`} className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
                View my scores <ArrowRight className="size-4" /></Link>
            </div>
            <div className="space-y-2 rounded-lg bg-muted/50 p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Next best action</div>
              {next ? (
                <>
                  <p className="font-medium">{next.title}</p>
                  <p className="text-sm text-primary">up to {next.points.toFixed(1)} points</p>
                  <div className="flex flex-wrap gap-2">
                    <Link href={`/scores?role=${card.baseline_id}&tab=plan`} className={buttonVariants({ size: "sm" })}>Start</Link>
                    <Button size="sm" variant="outline" onClick={() => markDone(next, true)}>Mark done</Button>
                  </div>
                </>
              ) : card.done_actions.length > 0 ? (
                <p className="text-sm text-muted-foreground">You&apos;ve marked every action in your plan done.</p>
              ) : (
                <>
                  <Link href={`/scores?role=${card.baseline_id}&tab=plan`} className={buttonVariants({ size: "sm", variant: "outline" })}>Open the plan</Link>
                </>
              )}
              {lastDone && (
                <p className="border-t pt-2 text-xs text-muted-foreground" data-testid="done-note">
                  Done: {lastDone.title}.{" "}
                  <button type="button" className="font-medium text-primary hover:underline" onClick={() => markDone(lastDone, false)}>Undo</button>
                  {PROOF_SECTION[lastDone.type] && (
                    <><br />{ADD_PROOF_HINT}{" "}
                      <button type="button" className="font-medium text-primary hover:underline" onClick={() => addProof(lastDone)}>Add proof</button>
                      <br />{THEN_CONFIRM}</>
                  )}</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {finish && (
        <Card className="border-primary/30 bg-primary/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-medium">Finish your profile</p>
              <p className="text-sm text-muted-foreground">Work authorization, start date, salary.</p>
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => {
                setDismissed(true);
                post(`/candidates/${cid}/application-details/dismiss`).catch((e) => toast.error(e.message));
              }}>Dismiss</Button>
              <Link href="/profile#application-details" className={buttonVariants({ size: "sm" })}>Add details</Link>
            </div>
          </CardContent>
        </Card>
      )}

      <JobsBrowser cid={cid} />
    </div>
  );
}
