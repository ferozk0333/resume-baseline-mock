"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { PostingPreview } from "@/components/app/jobs/posting-preview";
import { ApiError, get, post, WORKPLACE, type Baseline, type Candidate, type Posting, type Task } from "@/lib/api";

type Run = { id: string; baseline_id: string; baseline_title: string; status: string; started_at: string };

/** Admin → Demo tools (specs/17 §2): the old "Score a profile against a role" page. Candidates no longer see it. */
export default function DemoTools() {
  const router = useRouter();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [baselines, setBaselines] = useState<Baseline[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [baselineId, setBaselineId] = useState<string>("");
  const [runsFor, setRunsFor] = useState<{ id: string; runs: Run[] }>({ id: "", runs: [] });
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState<Posting[]>([]);
  const [applying, setApplying] = useState<string | null>(null);
  const [openJob, setOpenJob] = useState<string | null>(null);

  useEffect(() => {
    get<Posting[]>("/open-jobs").then(setJobs).catch(() => {});
    Promise.all([get<Candidate[]>("/candidates"), get<Baseline[]>("/baselines")])
      .then(([c, b]) => {
        setCandidates(c);
        setBaselines(b);
        if (b[0]?.id) setBaselineId(b[0].id);
      })
      .catch((e) => toast.error(`Could not reach the API: ${e.message}`));
  }, []);

  useEffect(() => {
    if (!selected) return;
    get<Run[]>(`/candidates/${selected}/runs`).then((runs) => setRunsFor({ id: selected, runs }))
      .catch(() => setRunsFor({ id: selected, runs: [] }));
  }, [selected]);
  const runs = runsFor.id === selected ? runsFor.runs : [];

  const target = baselines.find((b) => b.id === baselineId);

  async function createCandidate() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const c = await post<Candidate>("/candidates", { name: name.trim(), target_role: target?.title ?? null });
      router.push(`/candidates/${c.id}/onboard?baseline=${baselineId}`);
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  }

  async function apply(jobId: string) {
    if (!selected) return;
    setApplying(jobId);
    try {
      const { task_id } = await post<{ task_id: string }>(`/candidates/${selected}/apply`, { job_id: jobId });
      toast.info("Applied. Scoring against the job…");
      for (;;) {                                     // the run page shows progress once the run exists
        await new Promise((r) => setTimeout(r, 1000));
        const t = await get<Task>(`/tasks/${task_id}`);
        if (t.status === "error") throw new Error(t.error ?? "Scoring failed.");
        if (t.run_id) { router.push(`/runs/${t.run_id}`); return; }
      }
    } catch (e) {
      toast.error(e instanceof ApiError && e.status === 409 ? e.message : (e as Error).message);
      setApplying(null);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Demo tools: score a profile against a role</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a target role, then start with a resume or continue with a saved candidate.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Target role</CardTitle>
          <CardDescription>The baseline the profile is scored against.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {baselines.map((b) => (
            <button key={b.id} type="button" onClick={() => setBaselineId(b.id!)} aria-pressed={b.id === baselineId}
              className={`rounded-lg border px-4 py-3 text-left text-sm transition-colors ${
                b.id === baselineId ? "border-primary bg-primary/5 ring-2 ring-primary/30" : "bg-white hover:bg-muted"}`}>
              <div className="font-medium">{b.title}</div>
              <div className="text-xs text-muted-foreground">{b.level} · {b.required_years} yrs</div>
            </button>
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">New candidate</CardTitle>
            <CardDescription>Upload a resume on the next screen.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="cand-name">Name</Label>
              <Input id="cand-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Jane Doe"
                onKeyDown={(e) => e.key === "Enter" && createCandidate()} />
            </div>
            <Button onClick={createCandidate} disabled={!name.trim() || !baselineId || busy}>Start</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Saved candidates</CardTitle>
            <CardDescription>Review their profile again, or open a past score.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {candidates.length === 0 ? (
              <p className="text-sm text-muted-foreground">No candidates yet.</p>
            ) : (
              <div className="max-h-64 space-y-1 overflow-y-auto">
                {candidates.map((c) => (
                  <button key={c.id} type="button" onClick={() => setSelected(c.id)} aria-pressed={c.id === selected}
                    className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${
                      c.id === selected ? "bg-primary/10" : "hover:bg-muted"}`}>
                    <span className="font-medium">{c.name || "Unnamed"}</span>
                    <span className="text-xs text-muted-foreground">{c.target_role}</span>
                  </button>
                ))}
              </div>
            )}
            {selected && (
              <div className="space-y-2 border-t pt-3">
                <Button variant="outline" size="sm"
                  onClick={() => router.push(`/candidates/${selected}/onboard?baseline=${baselineId}&step=review`)}>
                  Review profile and score
                </Button>
                {runs.length > 0 && (
                  <ul className="space-y-1 text-sm">
                    {runs.slice(0, 5).map((r) => (
                      <li key={r.id}>
                        <button type="button" className="flex w-full items-center justify-between rounded-md px-2 py-1.5 hover:bg-muted"
                          onClick={() => router.push(`/runs/${r.id}`)}>
                          <span>{r.baseline_title}</span>
                          <span className="flex items-center gap-2 text-xs text-muted-foreground">
                            {new Date(r.started_at).toLocaleString()}
                            <Badge variant="secondary">{r.status}</Badge>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Apply to a job</CardTitle>
          <CardDescription>
            Open jobs. Applying uses the selected candidate&apos;s confirmed profile and scores it against the job; the
            recruiter sees the application.{!selected && " Pick a saved candidate first."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {jobs.length === 0 ? <p className="text-sm text-muted-foreground">No open jobs right now.</p> : (
            <ul aria-label="Open jobs" className="divide-y">
              {jobs.map((j) => (
                <li key={j.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <button type="button" className="min-w-0 text-left" onClick={() => setOpenJob(openJob === j.id ? null : j.id)} aria-expanded={openJob === j.id}>
                      <div className="font-medium hover:underline">{j.title}</div>
                      <div className="text-xs text-muted-foreground">{[j.company, j.location, j.workplace ? WORKPLACE[j.workplace] : null].filter(Boolean).join(" · ")}</div>
                    </button>
                    <Button size="sm" className="ml-auto" disabled={!selected || applying !== null} onClick={() => apply(j.id)}>
                      {applying === j.id ? "Scoring…" : "Apply"}
                    </Button>
                  </div>
                  {openJob === j.id && <div className="mt-3 rounded-lg border bg-muted/30 p-4"><PostingPreview p={j} /></div>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
