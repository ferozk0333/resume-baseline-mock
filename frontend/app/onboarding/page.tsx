"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, PartyPopper } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { DetailsForm } from "@/components/features/candidate/details-form";
import { GoodToKnow } from "@/components/features/candidate/good-to-know";
import { ReadingWait, ScoringWait } from "@/components/features/candidate/wait-progress";
import { EeatTip, RoleScoreCard } from "@/components/features/candidate/score-card";
import { Review } from "@/components/features/onboarding/review";
import { UploadForm } from "@/components/features/onboarding/upload-form";
import { ApiError, get, post, put, STEP_LABELS, type ApplicationDetails, type Candidate, type OnboardingState,
  type OnboardingStep, type ProfileResponse, type ScoreCard, type Target } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";
import { cn } from "@/lib/utils";

export default function OnboardingPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Onboarding /></Suspense>;
}

/** First run (specs/17 §3): five steps, each resumable; progress lives in the database. */
function Onboarding() {
  const router = useRouter();
  const [cid, ready, setCid] = useCurrentCandidate();
  const [state, setState] = useState<OnboardingState | null>(null);

  const load = useCallback(() => (!cid ? Promise.resolve() : get<OnboardingState>(`/candidates/${cid}/onboarding`)
    .then(setState).catch((e) => {
      if (e instanceof ApiError && e.status === 404) setCid(null);       // a profile deleted from the database
      else toast.error((e as Error).message);
    })), [cid, setCid]);
  useEffect(() => { if (ready && cid) load(); }, [ready, cid, load]);

  const go = useCallback(async (step: OnboardingStep) => {
    try { await put(`/candidates/${cid}/onboarding`, { step }); await load(); }
    catch (e) { toast.error((e as Error).message); }
  }, [cid, load]);

  if (!ready || (cid && !state)) return <Skeleton className="h-96" />;
  const step: OnboardingStep = !cid || !state ? "proof" : state.onboarding.step;
  const mine = state && state.candidate.id === cid ? state : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {step === "done" ? "You're set up" : mine?.candidate.name ? `Welcome, ${mine.candidate.name}` : "Set up your profile"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {step === "done" ? "Your scores and plan are saved." : null}
          </p>
        </div>
        {cid && step !== "done" && (
          <Link href="/" className={buttonVariants({ variant: "outline", size: "sm" })}>Save and exit</Link>
        )}
      </div>
      {/* Going back only moves the saved step: documents, roles, review edits and scores are all kept. Confirming the
          profile again on Review re-runs scoring (POST /baseline-scores). */}
      {step !== "done" && <Stepper current={step} onGo={mine ? go : undefined} />}

      {step === "proof" && <ProofStep cid={cid} name={mine?.candidate.name ?? ""}
        onStarted={async (_, id) => { if (id !== cid) setCid(id); else await load(); }} />}
      {step === "roles" && mine && <RolesStep state={mine} reload={load} onContinue={() => go("review")} onRetry={() => go("proof")} />}
      {step === "review" && mine && <ReviewStep cid={mine.candidate.id} onScored={load} onBack={() => go("roles")} />}
      {step === "scores" && mine && <ScoresStep state={mine} onContinue={() => go("details")} onBack={() => go("review")} />}
      {step === "details" && mine && <DetailsStep cid={mine.candidate.id} onDone={() => go("done")} />}
      {step === "done" && (
        <Card>
          <CardContent className="flex flex-col items-start gap-4 py-8">
            <PartyPopper className="size-8 text-primary" aria-hidden />
            <div>
              <p className="text-lg font-semibold">You&apos;re set up.</p>
              <p className="text-sm text-muted-foreground">Next: apply to jobs.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => router.push("/")}>Start applying</Button>
              <Link href="/scores?tab=plan" className={buttonVariants({ variant: "outline" })}>See my full plan</Link>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Stepper({ current, onGo }: { current: OnboardingStep; onGo?: (step: OnboardingStep) => void }) {
  const at = STEP_LABELS.findIndex((s) => s.key === current);
  return (
    <nav aria-label="Setup progress">
      <p className="mb-2 text-sm font-medium sm:hidden">Step {at + 1} of {STEP_LABELS.length}: {STEP_LABELS[at].label}</p>
      <ol className="flex items-center gap-1 sm:gap-2">
        {STEP_LABELS.map((s, i) => {
          const marker = (
            <>
              <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
                i < at && "border-primary bg-primary text-primary-foreground", i === at && "border-primary text-primary ring-2 ring-primary/20",
                i > at && "text-muted-foreground")}>
                {i < at ? <Check className="size-4" aria-label="done" /> : i + 1}
              </span>
              <span className={cn("hidden truncate text-sm sm:inline", i === at ? "font-medium" : "text-muted-foreground")}>
                {s.label}
              </span>
            </>
          );
          // Completed steps are links back, except "proof": going back there would restart the upload. Future steps stay
          // plain text.
          const canGo = !!onGo && i < at && s.key !== "proof";
          return (
            <li key={s.key} className="flex min-w-0 flex-1 items-center gap-2" aria-current={i === at ? "step" : undefined}>
              {canGo ? (
                <button type="button" onClick={() => onGo?.(s.key)} aria-label={`${s.label}, done. Go back to this step`}
                  className="flex min-w-0 items-center gap-2 rounded-md outline-none transition-opacity hover:opacity-80 focus-visible:ring-3 focus-visible:ring-ring/50">
                  {marker}
                </button>
              ) : (
                <span className="flex min-w-0 items-center gap-2">{marker}</span>
              )}
              {i < STEP_LABELS.length - 1 && <span className="h-px min-w-3 flex-1 bg-border" aria-hidden />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// --- 1. Add your proof -------------------------------------------------------------------------------------------

function ProofStep({ cid, name: saved, onStarted }: { cid: string | null; name: string;
  onStarted: (taskId: string, candidateId: string) => void }) {
  const [name, setName] = useState(saved);
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add your proof</CardTitle>
        </CardHeader>
        {!cid && (
          <CardContent>
            <div className="max-w-sm space-y-1.5">
              <Label htmlFor="your-name">Your name *</Label>
              <Input id="your-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Jane Doe" />
            </div>
          </CardContent>
        )}
      </Card>
      <UploadForm candidateId={cid ?? undefined} onboarding onStarted={onStarted}
        ensureCandidate={async () => {
          if (!name.trim()) throw new Error("Enter your name first.");
          return (await post<Candidate>("/candidates", { name: name.trim() })).id;
        }} />
    </div>
  );
}

// --- 2. Target roles (while the documents are read) --------------------------------------------------------------

function RolesStep({ state, reload, onContinue, onRetry }: { state: OnboardingState; reload: () => void;
  onContinue: () => void; onRetry: () => void }) {
  const cid = state.candidate.id;
  const [picked, setPicked] = useState<Target[]>(state.targets);
  const [resumeRead, setResumeRead] = useState(false);       // Review opens once the resume is read
  const done = state.read_task?.status === "done";
  const failed = state.read_task?.status === "error";
  const canReview = (done || resumeRead) && !failed;

  // Saves go out one at a time, in click order, and only the latest one's answer is shown: two quick clicks (add a
  // role, then make it primary) can never be stored in the wrong order.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const latest = useRef(0);
  function save(next: Target[]) {
    const before = picked;
    setPicked(next);
    if (next.length === 0) return;
    const n = ++latest.current;
    queue.current = queue.current.then(async () => {
      try {
        const out = await put<Target[]>(`/candidates/${cid}/targets`, { baseline_ids: next.map((t) => t.baseline_id),
          primary: next.find((t) => t.is_primary)?.baseline_id ?? next[0].baseline_id });
        if (n === latest.current) setPicked(out);
      } catch (e) { if (n === latest.current) setPicked(before); toast.error((e as Error).message); }
    });
  }
  function toggle(id: string, title: string) {
    const on = picked.some((t) => t.baseline_id === id);
    if (on) {
      const rest = picked.filter((t) => t.baseline_id !== id);
      save(rest.map((t, i) => ({ ...t, is_primary: rest.some((r) => r.is_primary) ? t.is_primary : i === 0 })));
    } else if (picked.length < 3) {
      save([...picked, { baseline_id: id, title, is_primary: picked.length === 0 }]);
    } else toast.info("Pick up to 3 roles.");
  }
  const makePrimary = (id: string) => save(picked.map((x) => ({ ...x, is_primary: x.baseline_id === id })));

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <Card className="h-fit">
        <CardHeader>
          <CardTitle className="text-base">Which roles are you aiming for?</CardTitle>
          <CardDescription>Pick 1 to 3.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div role="group" aria-label="Roles" className="grid gap-2 sm:grid-cols-2">
            {state.roles.map((r) => {
              const t = picked.find((x) => x.baseline_id === r.baseline_id);
              return (
                <div key={r.baseline_id} data-testid={`role-card-${r.baseline_id}`}
                  className={cn("flex items-center gap-2 rounded-lg border pr-3 text-sm transition-colors",
                    t ? "border-primary bg-primary/5 ring-2 ring-primary/30" : "bg-white hover:bg-muted")}>
                  <button type="button" aria-pressed={!!t} onClick={() => toggle(r.baseline_id, r.title)}
                    className="min-w-0 flex-1 rounded-lg px-4 py-3 text-left font-medium">{r.title}</button>
                  {t?.is_primary && <Badge>Primary</Badge>}
                  {t && !t.is_primary && (
                    <button type="button" onClick={() => makePrimary(r.baseline_id)} aria-label={`Make ${r.title} primary`}
                      className="shrink-0 text-xs font-medium text-primary hover:underline">Make primary</button>
                  )}
                </div>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3 border-t pt-4">
            {!canReview && !failed && picked.length > 0 && (
              <span className="text-sm text-muted-foreground">You can continue when your resume is read.</span>)}
            <Button disabled={!canReview || picked.length === 0} onClick={onContinue}>Continue to review</Button>
          </div>
        </CardContent>
      </Card>
      <div className="space-y-4">
        <ReadingWait cid={cid} readTaskId={state.onboarding.read_task_id} onDone={reload}
          onReviewReady={() => setResumeRead(true)} />
        {failed && <Button variant="outline" size="sm" onClick={onRetry}>Upload again</Button>}
        <GoodToKnow kind="reading" />
      </div>
    </div>
  );
}

// --- 3. Review ----------------------------------------------------------------------------------------------------

function ReviewStep({ cid, onScored, onBack }: { cid: string; onScored: () => void; onBack: () => void }) {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const reading = !!profile?.reading;
  useEffect(() => {
    get<ProfileResponse>(`/candidates/${cid}/profile`).then(setProfile).catch((e) => toast.error(e.message));
  }, [cid]);
  useEffect(() => {                         // sources still being read join Review as each finishes
    if (!reading) return;
    const t = setInterval(() => get<ProfileResponse>(`/candidates/${cid}/profile`).then(setProfile).catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [cid, reading]);
  if (!profile) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-3">
      {/* Back keeps every edit made here; confirming the profile again later re-runs scoring. */}
      <Button variant="ghost" size="sm" onClick={onBack} disabled={busy}>Back to target roles</Button>
      <Review data={profile} onChange={setProfile} confirming={busy} confirmLabel="Confirm profile" onConfirm={async () => {
        setBusy(true);
        try { await post(`/candidates/${cid}/baseline-scores`); onScored(); }
        catch (e) { toast.error((e as Error).message); setBusy(false); }
      }} />
    </div>
  );
}

// --- 4. Your baseline scores ----------------------------------------------------------------------------------------

function ScoresStep({ state, onContinue, onBack }: { state: OnboardingState; onContinue: () => void; onBack: () => void }) {
  const cid = state.candidate.id;
  const [cards, setCards] = useState<ScoreCard[] | null>(null);
  // Back on this step after the scores were ready (a reload): straight to the results.
  const [results, setResults] = useState(state.score_task?.status === "done"
    && !Object.keys((state.score_task.result as { failed?: object } | null)?.failed ?? {}).length);
  const loadCards = useCallback(() => {
    get<ScoreCard[]>(`/candidates/${cid}/baseline-scores`).then(setCards).catch((e) => toast.error(e.message));
  }, [cid]);
  useEffect(() => { if (results) loadCards(); }, [results, loadCards]);
  const waiting = cards?.some((c) => c.plan_status === "generating" || c.status === "running");
  useEffect(() => {                                       // the primary role's plan is written just after its score
    if (!waiting) return;
    const t = setInterval(loadCards, 2000);
    return () => clearInterval(t);
  }, [waiting, loadCards]);
  const advance = useCallback(() => setResults(true), []);

  if (!results) {
    return (
      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <div className="space-y-3">
          <ScoringWait cid={cid} onAdvance={advance} />
          {state.score_task?.status === "error" && (
            <Button variant="ghost" size="sm" onClick={onBack}>Back to review</Button>)}
        </div>
        <GoodToKnow kind="scoring" />
      </div>
    );
  }
  if (!cards) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Scores from 0 to 100 (<EeatTip />).
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {cards.map((c) => <RoleScoreCard key={c.baseline_id} card={c} />)}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Back keeps these scores; they change only if the profile is confirmed again on Review. */}
        <Button variant="ghost" size="sm" onClick={onBack}>Back to review</Button>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted-foreground">Your recommendations are saved under My scores.</span>
          <Button onClick={onContinue}>Continue</Button>
        </div>
      </div>
    </div>
  );
}

// --- 5. Finish your profile (optional) ------------------------------------------------------------------------------

function DetailsStep({ cid, onDone }: { cid: string; onDone: () => void }) {
  const [details, setDetails] = useState<ApplicationDetails | null>(null);
  useEffect(() => {
    get<ApplicationDetails>(`/candidates/${cid}/application-details`).then(setDetails).catch((e) => toast.error(e.message));
  }, [cid]);
  if (!details) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Optional.</p>
      <DetailsForm candidateId={cid} initial={details} onSaved={onDone}
        extra={<Button type="button" variant="outline" onClick={onDone}>Skip for now</Button>} />
    </div>
  );
}
