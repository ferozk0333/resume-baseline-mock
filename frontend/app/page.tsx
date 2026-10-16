"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PercentileLine } from "@/components/app/candidate/score-card";
import { PostingPreview } from "@/components/app/jobs/posting-preview";
import { ApiError, get, post, STEP_LABELS, WORKPLACE, type OnboardingState, type Posting, type ScoreCard } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";

export default function HomePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Home /></Suspense>;
}

/** Candidate Home (specs/17 §4). Phase A: the baseline score strip, "Finish your profile", and open jobs to apply to;
 * the ranked "Jobs for you" list with job detail comes in phase B. */
function Home() {
  const router = useRouter();
  const [cid, ready, setCid] = useCurrentCandidate();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [cards, setCards] = useState<ScoreCard[] | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!ready) return;
    if (!cid) { router.replace("/onboarding"); return; }           // first run
    get<OnboardingState>(`/candidates/${cid}/onboarding`).then((s) => {
      setState(s);
      if (s.onboarding.step === "done") get<ScoreCard[]>(`/candidates/${cid}/baseline-scores`).then(setCards).catch(() => setCards([]));
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
          <CardDescription>Step {at + 1} of {STEP_LABELS.length}: {STEP_LABELS[at].label}. Your progress is saved.</CardDescription>
        </CardHeader>
        <CardContent><Link href="/onboarding" className={buttonVariants()}>Continue</Link></CardContent>
      </Card>
    );
  }
  const card = cards?.find((c) => c.baseline_id === role) ?? cards?.[0];
  const next = card?.actions[0];
  const finish = !state.details.saved_at && !state.details.dismissed_at && !dismissed;

  return (
    <div className="space-y-6">
      <h1 className="sr-only">Home</h1>
      {!cards ? <Skeleton className="h-28" /> : card && (
        <Card>
          <CardContent className="grid gap-5 md:grid-cols-2">
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor="strip-role" className="sr-only">Role</label>
                {cards.length > 1 ? (
                  <select id="strip-role" value={card.baseline_id} onChange={(e) => setRole(e.target.value)}
                    className="h-8 rounded-lg border border-input bg-white px-2 text-sm font-medium">
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
                  <Link href={`/scores?role=${card.baseline_id}&tab=plan`} className={buttonVariants({ size: "sm" })}>Start</Link>
                </>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground">Open your plan for {card.title} to see what adds the most.</p>
                  <Link href={`/scores?role=${card.baseline_id}&tab=plan`} className={buttonVariants({ size: "sm", variant: "outline" })}>Open the plan</Link>
                </>
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
              <p className="text-sm text-muted-foreground">Answer the questions most applications ask, once: work authorization, start date, salary.</p>
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

      <OpenJobs cid={cid} />
    </div>
  );
}

/** Open recruiter jobs. Applying uses the confirmed profile; the candidate never sees the recruiter's job score. */
function OpenJobs({ cid }: { cid: string }) {
  const [jobs, setJobs] = useState<Posting[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  useEffect(() => { get<Posting[]>("/open-jobs").then(setJobs).catch(() => setJobs([])); }, []);
  const apply = useCallback(async (jobId: string) => {
    setBusy(jobId);
    try {
      await post(`/candidates/${cid}/apply`, { job_id: jobId });
      setApplied((s) => new Set(s).add(jobId));
      toast.success("Applied. The recruiter can see your application now.");
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(null); }
  }, [cid]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Open jobs</CardTitle>
        <CardDescription>Jobs posted by recruiters on RightStaff.</CardDescription>
      </CardHeader>
      <CardContent>
        {jobs === null ? <Skeleton className="h-24" /> : jobs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open jobs right now.</p>
        ) : (
          <ul aria-label="Open jobs" className="divide-y">
            {jobs.map((j) => (
              <li key={j.id} className="py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setOpen(open === j.id ? null : j.id)}
                    aria-expanded={open === j.id}>
                    <div className="font-medium hover:underline">{j.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {[j.company, j.location, j.workplace ? WORKPLACE[j.workplace] : null].filter(Boolean).join(" · ")}</div>
                  </button>
                  {applied.has(j.id) ? <Badge variant="secondary">Applied</Badge> : (
                    <Button size="sm" disabled={busy !== null} onClick={() => apply(j.id)}>{busy === j.id ? "Applying…" : "Apply"}</Button>
                  )}
                </div>
                {open === j.id && <div className="mt-3 rounded-lg border bg-muted/30 p-4"><PostingPreview p={j} /></div>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
