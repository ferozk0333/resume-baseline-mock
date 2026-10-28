"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { InterviewPlan } from "@/components/features/recruiter/interview-plan";
import { WeightAssistant } from "@/components/features/recruiter/weight-assistant";
import { DIMENSIONS, get, put, titleCase, type Job, type Weights } from "@/lib/api";

export default function SettingsPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Settings /></Suspense>;
}

function Settings() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    get<Job>(`/jobs/${id}`).then(setJob).catch((e) => toast.error(e.message));
    get<{ labels: Record<string, string> }>("/config/weights").then((d) => setLabels(d.labels)).catch(() => {});
  }, [id]);
  if (!job) return <Skeleton className="h-96" />;

  async function save(update: Partial<{ weights: Weights; must_haves: string[]; preset: string }>) {
    try { setJob({ ...job!, ...(await put<Job>(`/jobs/${id}`, update)) }); }
    catch (e) { toast.error((e as Error).message); }
  }
  const w = job.weights;
  const one = (v: number | readonly number[]) => (Array.isArray(v) ? v[0] : v) as number;
  const dim = (d: string, v: number | readonly number[]): Weights => ({ ...w, dimensions: { ...w.dimensions, [d]: one(v) } });
  const sub = (d: string, s: string, v: number | readonly number[]): Weights =>
    ({ ...w, subcategories: { ...w.subcategories, [d]: { ...w.subcategories[d], [s]: one(v) } } });
  const total = DIMENSIONS.reduce((s, d) => s + w.dimensions[d], 0);

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/recruiter/jobs/${id}`} className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Applicants
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Job settings · {job.title}</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Weights</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Presets:</span>
            {(job.presets ?? []).map((p) => (
              <Button key={p} size="sm" variant={job.preset === p ? "default" : "outline"} onClick={() => save({ preset: p })}>{p}</Button>
            ))}
          </div>
          <WeightAssistant job={job} onJob={(j) => setJob({ ...job, ...j })} />
          <h3 className="text-sm font-medium">Fine-tune</h3>
          <div className="grid gap-5 md:grid-cols-2">
            {DIMENSIONS.map((d) => (
              <div key={d} className="space-y-2">
                <div className="flex justify-between text-sm"><span id={`d-${d}`}>{titleCase(d)}</span>
                  <span className="tabular-nums text-muted-foreground">{Math.round(w.dimensions[d])} · {total ? Math.round(100 * w.dimensions[d] / total) : 0}%</span></div>
                <Slider aria-labelledby={`d-${d}`} min={0} max={100} step={1} value={[Math.round(w.dimensions[d])]}
                  onValueChange={(v) => setJob({ ...job, preset: null, weights: dim(d, v) })}
                  onValueCommitted={(v) => save({ weights: dim(d, v) })} />
              </div>
            ))}
          </div>
          <details className="rounded-lg border">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Advanced: subcategory weights</summary>
            <div className="grid gap-5 border-t p-3 md:grid-cols-2">
              {DIMENSIONS.flatMap((d) => Object.entries(w.subcategories[d]).map(([s, v]) => (
                <div key={s} className="space-y-2">
                  <div className="flex justify-between text-sm"><span id={`s-${s}`}>{labels[s] ?? s}</span>
                    <span className="tabular-nums text-muted-foreground">{Math.round(v)}</span></div>
                  <Slider aria-labelledby={`s-${s}`} min={0} max={100} step={1} value={[Math.round(v)]}
                    onValueChange={(x) => setJob({ ...job, preset: null, weights: sub(d, s, x) })}
                    onValueCommitted={(x) => save({ weights: sub(d, s, x) })} />
                </div>
              )))}
            </div>
          </details>
        </CardContent>
      </Card>

      <InterviewPlan jobId={id} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Must-haves</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2 md:grid-cols-2">
            {job.baseline?.criteria.map((c) => (
              <li key={c.id}>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={job.must_haves.includes(c.id!)} onChange={(e) => save({
                    must_haves: e.target.checked ? [...job.must_haves, c.id!] : job.must_haves.filter((m) => m !== c.id) })} />
                  {c.name} <Badge variant="outline" className="text-[10px]">{c.kind}</Badge>
                  <Badge variant="outline" className="text-[10px]">{c.priority}</Badge>
                </label>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
