"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, CircleAlert, Loader2, PartyPopper } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { DetailsForm } from "@/components/app/candidate/details-form";
import { EeatTip, RoleScoreCard } from "@/components/app/candidate/score-card";
import { Review } from "@/components/app/onboarding/review";
import { UploadForm } from "@/components/app/onboarding/upload-form";
import { ProgressPanel } from "@/components/app/progress-panel";
import { useTask, type TaskState } from "@/hooks/use-task";
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
            {step === "done" ? "Your scores and plan are saved." : "Show your proof once. See where you stand for the roles you want, and how to get stronger."}
          </p>
        </div>
        {cid && step !== "done" && (
          <Link href="/" className={buttonVariants({ variant: "outline", size: "sm" })}>Save and exit</Link>
        )}
      </div>
      {step !== "done" && <Stepper current={step} />}

      {step === "proof" && <ProofStep cid={cid} name={mine?.candidate.name ?? ""}
        onStarted={async (_, id) => { if (id !== cid) setCid(id); else await load(); }} />}
      {step === "roles" && mine && <RolesStep state={mine} reload={load} onContinue={() => go("review")} onRetry={() => go("proof")} />}
      {step === "review" && mine && <ReviewStep cid={mine.candidate.id} onScored={load} />}
      {step === "scores" && mine && <ScoresStep state={mine} onContinue={() => go("details")} onBack={() => go("review")} />}
      {step === "details" && mine && <DetailsStep cid={mine.candidate.id} onDone={() => go("done")} />}
      {step === "done" && (
        <Card>
          <CardContent className="flex flex-col items-start gap-4 py-8">
            <PartyPopper className="size-8 text-primary" aria-hidden />
            <div>
              <p className="text-lg font-semibold">You&apos;re set up.</p>
              <p className="text-sm text-muted-foreground">Next: apply to jobs. Your baseline scores and plan stay under My scores.</p>
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

function Stepper({ current }: { current: OnboardingStep }) {
  const at = STEP_LABELS.findIndex((s) => s.key === current);
  return (
    <nav aria-label="Setup progress">
      <p className="mb-2 text-sm font-medium sm:hidden">Step {at + 1} of {STEP_LABELS.length}: {STEP_LABELS[at].label}</p>
      <ol className="flex items-center gap-1 sm:gap-2">
        {STEP_LABELS.map((s, i) => (
          <li key={s.key} className="flex min-w-0 flex-1 items-center gap-2" aria-current={i === at ? "step" : undefined}>
            <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
              i < at && "border-primary bg-primary text-primary-foreground", i === at && "border-primary text-primary ring-2 ring-primary/20",
              i > at && "text-muted-foreground")}>
              {i < at ? <Check className="size-4" aria-label="done" /> : i + 1}
            </span>
            <span className={cn("hidden truncate text-sm sm:inline", i === at ? "font-medium" : "text-muted-foreground")}>
              {s.label}
            </span>
            {i < STEP_LABELS.length - 1 && <span className="h-px min-w-3 flex-1 bg-border" aria-hidden />}
          </li>
        ))}
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
          <CardDescription>Your resume is enough to start. Every extra source below makes your scores more trustworthy.</CardDescription>
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
  const reading = useTask(state.onboarding.read_task_id, () => reload());
  const done = reading.status === "done" || state.read_task?.status === "done";
  const failed = reading.status === "error" || state.read_task?.status === "error";

  async function save(next: Target[]) {
    const before = picked;
    setPicked(next);
    if (next.length === 0) return;
    try {
      const out = await put<Target[]>(`/candidates/${cid}/targets`, { baseline_ids: next.map((t) => t.baseline_id),
        primary: next.find((t) => t.is_primary)?.baseline_id ?? next[0].baseline_id });
      setPicked(out);
    } catch (e) { setPicked(before); toast.error((e as Error).message); }
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

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Which roles are you aiming for?</CardTitle>
          <CardDescription>Pick 1 to 3. We score your proof against each role. Your primary role leads your Home page.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div role="group" aria-label="Roles" className="grid gap-2 sm:grid-cols-2">
            {state.roles.map((r) => {
              const t = picked.find((x) => x.baseline_id === r.baseline_id);
              return (
                <button key={r.baseline_id} type="button" aria-pressed={!!t} onClick={() => toggle(r.baseline_id, r.title)}
                  className={cn("flex items-center justify-between gap-2 rounded-lg border px-4 py-3 text-left text-sm transition-colors",
                    t ? "border-primary bg-primary/5 ring-2 ring-primary/30" : "bg-white hover:bg-muted")}>
                  <span className="font-medium">{r.title}</span>
                  {t?.is_primary && <Badge>Primary</Badge>}
                </button>
              );
            })}
          </div>
          {picked.length > 1 && (
            <fieldset className="space-y-1.5">
              <legend className="text-sm font-medium">Primary role</legend>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {picked.map((t) => (
                  <label key={t.baseline_id} className="flex items-center gap-1.5 text-sm">
                    <input type="radio" name="primary" checked={t.is_primary}
                      onChange={() => save(picked.map((x) => ({ ...x, is_primary: x.baseline_id === t.baseline_id })))} />
                    {t.title}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <div className="flex flex-wrap items-center justify-end gap-3 border-t pt-4">
            {!done && !failed && picked.length > 0 && (
              <span className="text-sm text-muted-foreground">You can continue when your documents are read.</span>)}
            <Button disabled={!done || picked.length === 0} onClick={onContinue}>Continue to review</Button>
          </div>
        </CardContent>
      </Card>
      <ReadingPanel task={reading} failed={failed} onRetry={onRetry}
        message={state.read_task?.status === "error" ? state.read_task.error : null} />
    </div>
  );
}

function ReadingPanel({ task, failed, onRetry, message }: { task: TaskState; failed: boolean; onRetry: () => void;
  message: string | null }) {
  const done = task.status === "done";
  return (
    <Card aria-live="polite" className="h-fit">
      <CardHeader className="flex flex-row items-center gap-2 space-y-0">
        {failed ? <CircleAlert className="size-5 text-destructive" /> : done ? <Check className="size-5 text-emerald-600" />
          : <Loader2 className="size-5 animate-spin text-primary" />}
        <CardTitle className="text-base">{failed ? "We couldn't read your documents" : done ? "Your documents are read" : "Reading your documents"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Reading progress"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(done ? 100 : task.percent)}>
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${done ? 100 : task.percent}%` }} />
        </div>
        <p className="text-sm text-muted-foreground">
          {failed ? (message ?? task.message) : done ? "Next, check what we found." : task.message ?? "Starting…"}
        </p>
        {failed && <Button variant="outline" size="sm" onClick={onRetry}>Upload again</Button>}
        {!done && !failed && <p className="text-xs text-muted-foreground">This takes a minute or two. Pick your roles meanwhile.</p>}
      </CardContent>
    </Card>
  );
}

// --- 3. Review ----------------------------------------------------------------------------------------------------

function ReviewStep({ cid, onScored }: { cid: string; onScored: () => void }) {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    get<ProfileResponse>(`/candidates/${cid}/profile`).then(setProfile).catch((e) => toast.error(e.message));
  }, [cid]);
  if (!profile) return <Skeleton className="h-96" />;
  return (
    <Review data={profile} onChange={setProfile} confirming={busy} confirmLabel="Confirm profile" onConfirm={async () => {
      setBusy(true);
      try { await post(`/candidates/${cid}/baseline-scores`); onScored(); }
      catch (e) { toast.error((e as Error).message); setBusy(false); }
    }} />
  );
}

// --- 4. Your baseline scores ----------------------------------------------------------------------------------------

function ScoresStep({ state, onContinue, onBack }: { state: OnboardingState; onContinue: () => void; onBack: () => void }) {
  const cid = state.candidate.id;
  const [cards, setCards] = useState<ScoreCard[] | null>(null);
  const loadCards = useCallback(() => {
    get<ScoreCard[]>(`/candidates/${cid}/baseline-scores`).then(setCards).catch((e) => toast.error(e.message));
  }, [cid]);
  const scoring = useTask(state.onboarding.score_task_id, () => loadCards());
  const finished = scoring.status === "done" || state.score_task?.status === "done";
  const failed = scoring.status === "error" || state.score_task?.status === "error";
  useEffect(() => { if (finished) loadCards(); }, [finished, loadCards]);
  const waiting = cards?.some((c) => c.plan_status === "generating" || c.status === "running");
  useEffect(() => {                                       // the primary role's plan is written just after its score
    if (!waiting) return;
    const t = setInterval(loadCards, 2000);
    return () => clearInterval(t);
  }, [waiting, loadCards]);

  if (failed) {
    return (
      <Card><CardContent className="space-y-3 py-6">
        <p className="text-sm text-destructive">{state.score_task?.error ?? scoring.message ?? "Scoring stopped."}</p>
        <Button variant="outline" onClick={onBack}>Back to review</Button>
      </CardContent></Card>
    );
  }
  if (!finished || !cards) {
    return <ProgressPanel title={`Scoring your profile for ${state.targets.length === 1 ? state.targets[0].title
      : `${state.targets.length} roles`}`} task={scoring} />;
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Each baseline score uses <EeatTip /> to compare your proof with what the role usually asks for, from 0 to 100.
        It shows where you stand on average for the role, not for one specific job.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {cards.map((c) => <RoleScoreCard key={c.baseline_id} card={c} />)}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-3">
        <span className="text-sm text-muted-foreground">Your recommendations are saved under My scores.</span>
        <Button onClick={onContinue}>Continue</Button>
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
      <p className="text-sm text-muted-foreground">Optional. Answer the questions most applications ask, once. You can change them any time under Profile.</p>
      <DetailsForm candidateId={cid} initial={details} onSaved={onDone}
        extra={<Button type="button" variant="outline" onClick={onDone}>Skip for now</Button>} />
    </div>
  );
}
