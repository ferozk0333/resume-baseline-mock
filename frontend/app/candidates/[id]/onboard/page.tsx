"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { ProgressPanel } from "@/components/app/progress-panel";
import { Review } from "@/components/app/onboarding/review";
import { UploadForm } from "@/components/app/onboarding/upload-form";
import { useTask } from "@/hooks/use-task";
import { get, post, type Baseline, type ProfileResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

type Step = "upload" | "reading" | "review" | "scoring";
const STEPS = [{ key: "upload", label: "Upload" }, { key: "review", label: "Review" }, { key: "score", label: "Score" }];

export default function OnboardPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Onboard /></Suspense>;
}

function Onboard() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const baselineId = search.get("baseline") ?? "";
  const [step, setStep] = useState<Step>(search.get("step") === "review" ? "review" : "upload");
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [readTask, setReadTask] = useState<string | null>(null);
  const [scoreTask, setScoreTask] = useState<string | null>(null);

  useEffect(() => {
    if (baselineId) get<Baseline>(`/baselines/${baselineId}`).then(setBaseline).catch(() => setBaseline(null));
  }, [baselineId]);

  const loadProfile = useCallback(() => {
    get<ProfileResponse>(`/candidates/${id}/profile`).then(setProfile).catch((e) => toast.error(e.message));
  }, [id]);
  useEffect(() => { if (step === "review") loadProfile(); }, [step, loadProfile]);

  const reading = useTask(readTask, (status, last) => {
    if (status === "done") setStep("review");
    else { toast.error(last.message); setStep("upload"); }
  });
  const scoring = useTask(scoreTask, async (status, last) => {
    if (status === "error") { toast.error(last.message); setStep("review"); return; }
    const task = await get<{ result: { run_id: string; paused?: string } }>(`/tasks/${scoreTask}`);
    if (task.result.paused) toast.warning(task.result.paused);
    router.push(`/runs/${task.result.run_id}?technical=1`);
  });

  async function confirm() {
    try {
      const { task_id } = await post<{ task_id: string }>(`/candidates/${id}/score`, { baseline_id: baselineId });
      setScoreTask(task_id);
      setStep("scoring");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const active = step === "upload" || step === "reading" ? 0 : step === "review" ? 1 : 2;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{profile?.candidate.name ?? "New profile"}</h1>
          <p className="text-sm text-muted-foreground">Target role: {baseline?.title ?? "—"}</p>
        </div>
        <ol className="flex items-center gap-2 text-sm" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li key={s.key} className="flex items-center gap-2" aria-current={i === active ? "step" : undefined}>
              <span className={cn("flex size-6 items-center justify-center rounded-full border text-xs font-medium",
                i < active && "border-primary bg-primary text-primary-foreground",
                i === active && "border-primary text-primary")}>{i + 1}</span>
              <span className={i === active ? "font-medium" : "text-muted-foreground"}>{s.label}</span>
              {i < STEPS.length - 1 && <span className="mx-1 h-px w-8 bg-border" />}
            </li>
          ))}
        </ol>
      </div>

      {step === "upload" && <UploadForm candidateId={id} baselineId={baselineId}
        onStarted={(t) => { setReadTask(t); setStep("reading"); }} />}
      {step === "reading" && <ProgressPanel title="Reading your documents" task={reading} />}
      {step === "review" && (profile ? (
        <Review data={profile} onChange={setProfile} onConfirm={confirm} confirming={!!scoreTask} />
      ) : <Skeleton className="h-96" />)}
      {step === "scoring" && <ProgressPanel title="Scoring your profile" task={scoring} />}
    </div>
  );
}
