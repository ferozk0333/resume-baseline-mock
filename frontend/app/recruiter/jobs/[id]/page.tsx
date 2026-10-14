"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Columns3, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApplicantList } from "@/components/app/recruiter/applicant-list";
import { ProfilePanel } from "@/components/app/recruiter/profile-panel";
import { get, patch, type ApplicantRow, type Job, type Panel } from "@/lib/api";

const selectClass = "h-8 rounded-lg border border-input bg-white px-2 text-sm";

export default function JobPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><JobPageInner /></Suspense>;
}

function JobPageInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [rows, setRows] = useState<ApplicantRow[] | null>(null);
  const [sort, setSort] = useState("match");
  const [status, setStatus] = useState("");
  const [allMust, setAllMust] = useState(false);
  const [minScore, setMinScore] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [picked, setPicked] = useState<string[]>([]);

  useEffect(() => { get<Job>(`/jobs/${id}`).then(setJob).catch((e) => toast.error(e.message)); }, [id]);

  const loadRows = useCallback(() => {
    const q = new URLSearchParams({ sort });
    if (status) q.set("status", status);
    if (allMust) q.set("all_must_haves", "true");
    if (minScore) q.set("min_score", minScore);
    get<{ applicants: ApplicantRow[] }>(`/jobs/${id}/applicants?${q}`).then((d) => {
      setRows(d.applicants);
      setSelected((s) => s ?? d.applicants[0]?.application_id ?? null);
    }).catch((e) => toast.error(e.message));
  }, [id, sort, status, allMust, minScore]);
  useEffect(() => { loadRows(); }, [loadRows]);

  useEffect(() => {
    if (!selected) return;
    get<Panel>(`/applications/${selected}`).then(setPanel).catch((e) => toast.error(e.message));
  }, [selected]);

  async function changeStatus(s: Panel["status"]) {
    if (!panel) return;
    await patch(`/applications/${panel.application_id}`, { status: s });
    setPanel({ ...panel, status: s });
    loadRows();
  }

  const weightsLabel = job?.preset ? `${job.preset} preset` : "the team weights table";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href="/recruiter" className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" /> Jobs
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">{job?.title ?? "…"}</h1>
          <p className="text-sm text-muted-foreground">{job?.company} · ranked with {weightsLabel}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" disabled={picked.length < 2 || picked.length > 3}
            onClick={() => router.push(`/recruiter/jobs/${id}/compare?ids=${picked.join(",")}`)}>
            <Columns3 /> Compare{picked.length ? ` (${picked.length})` : ""}
          </Button>
          <Link href={`/recruiter/jobs/${id}/settings`} className={buttonVariants({ variant: "outline" })}>
            <Settings2 /> Job settings
          </Link>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(22rem,26rem)_1fr]">
        <Card className="gap-0 overflow-hidden py-0">
          <div className="flex flex-wrap items-center gap-2 border-b bg-muted/40 p-3 text-sm">
            <Label htmlFor="sort" className="sr-only">Sort by</Label>
            <select id="sort" value={sort} onChange={(e) => setSort(e.target.value)} className={selectClass}>
              <option value="match">Sort: match</option>
              <option value="confidence">Sort: confidence</option>
              <option value="must_haves">Sort: must-haves</option>
            </select>
            <Label htmlFor="status" className="sr-only">Status</Label>
            <select id="status" value={status} onChange={(e) => setStatus(e.target.value)} className={selectClass}>
              <option value="">All statuses</option>
              <option value="new">New</option>
              <option value="shortlisted">Shortlisted</option>
              <option value="not_moving_forward">Not moving forward</option>
            </select>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={allMust} onChange={(e) => setAllMust(e.target.checked)} /> All must-haves
            </label>
            <Label htmlFor="min" className="sr-only">Minimum score</Label>
            <Input id="min" type="number" min={0} max={100} placeholder="Min score" value={minScore}
              onChange={(e) => setMinScore(e.target.value)} className="h-8 w-24 bg-white" />
          </div>
          {rows === null ? <Skeleton className="m-3 h-64" /> : rows.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">No applicants match these filters.</p>
          ) : (
            <ApplicantList rows={rows} selected={selected} onSelect={setSelected} picked={picked}
              onPick={(a) => setPicked((p) => (p.includes(a) ? p.filter((x) => x !== a) : p.length < 3 ? [...p, a] : p))} />
          )}
        </Card>
        <Card className="h-[calc(100vh-11rem)] min-h-96 overflow-y-auto p-6">
          {panel ? <ProfilePanel p={panel} onStatus={changeStatus} /> : <Skeleton className="h-96" />}
        </Card>
      </div>
    </div>
  );
}
