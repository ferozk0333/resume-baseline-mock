"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, CalendarClock, Columns3, Pencil, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApplicantList } from "@/components/features/recruiter/applicant-list";
import { RejectDrawer, StageEmailDrawer } from "@/components/features/recruiter/decision-drawers";
import { useStartLoop } from "@/components/features/recruiter/start-loop";
import { PipelineBoard } from "@/components/features/recruiter/pipeline-board";
import { JobDescription } from "@/components/features/jobs/job-description";
import { ProfilePanel, TABS, type Tab } from "@/components/features/recruiter/profile-panel";
import { get, patch, post, type ApplicantRow, type Job, type Panel, type Stage } from "@/lib/api";
import { cn } from "@/lib/utils";

const selectClass = "h-8 rounded-lg border border-field-border bg-field px-2 text-sm";
type View = "ranked" | "pipeline" | "jd";
type Nav = { view: View; tab: Tab; app: string | null };

export default function JobPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Workspace /></Suspense>;
}

function Workspace() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  // View, tab and selected applicant live in state and are mirrored to the URL synchronously, so fast clicks
  // and ↑/↓ never read a stale URL. The tab persists when switching candidates.
  const [nav, setNav] = useState<Nav>(() => ({ view: (["pipeline", "jd"].includes(params.get("view") ?? "") ? params.get("view") : "ranked") as View,
    tab: (TABS as readonly string[]).includes(params.get("tab") ?? "") ? params.get("tab") as Tab : "overview",
    app: params.get("app") }));
  const { view, tab } = nav;
  const selectedParam = nav.app;

  const [job, setJob] = useState<Job | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [rows, setRows] = useState<ApplicantRow[] | null>(null);
  const [sort, setSort] = useState("match");
  const [status, setStatus] = useState("");
  const [allMust, setAllMust] = useState(false);
  const [minScore, setMinScore] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [drawer, setDrawer] = useState<"reject" | "stage" | null>(null);
  const [activityKey, setActivityKey] = useState(0);

  const setUrl = useCallback((patchNav: Partial<Nav>) => setNav((cur) => ({ ...cur, ...patchNav })), []);
  useEffect(() => {                                     // mirror the state into the URL (shareable, survives reload)
    const q = new URLSearchParams();
    if (nav.view !== "ranked") q.set("view", nav.view);
    if (nav.app) q.set("app", nav.app);
    if (nav.tab !== "overview") q.set("tab", nav.tab);
    const url = `${pathname}${q.size ? `?${q}` : ""}`;
    if (url !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", url);
  }, [nav, pathname]);

  useEffect(() => {
    get<Job>(`/jobs/${id}`).then(setJob).catch((e) => toast.error(e.message));
    get<{ stages: Stage[] }>(`/jobs/${id}/settings`).then((d) => setStages(d.stages)).catch(() => {});
  }, [id]);

  const filterKey = `${sort}|${status}|${allMust}|${minScore}`;
  const loadRows = useCallback(() => {
    const q = new URLSearchParams({ sort });
    if (status) q.set("status", status);
    if (allMust) q.set("all_must_haves", "true");
    if (minScore) q.set("min_score", minScore);
    get<{ applicants: ApplicantRow[] }>(`/jobs/${id}/applicants?${q}`).then((d) => setRows(d.applicants))
      .catch((e) => toast.error(e.message));
  }, [id, sort, status, allMust, minScore]);
  useEffect(() => { loadRows(); }, [loadRows]);

  const selected = selectedParam ?? rows?.[0]?.application_id ?? null;
  const loadPanel = useCallback(() => {
    if (selected) get<Panel>(`/applications/${selected}`).then(setPanel).catch((e) => toast.error(e.message));
  }, [selected]);
  useEffect(() => { loadPanel(); }, [loadPanel]);
  useEffect(() => {                    // "Recruiter active" signal for candidates (specs/17 §4.1), once per opened panel
    if (selected) post(`/applications/${selected}/opened`).catch(() => {});
  }, [selected]);

  const refresh = useCallback(() => { loadRows(); loadPanel(); setActivityKey((k) => k + 1); }, [loadRows, loadPanel]);
  const select = useCallback((appId: string) => setUrl({ app: appId }), [setUrl]);

  // ↑/↓ move through the list (not while typing).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (view !== "ranked" || !rows?.length || drawer) return;
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable=true]")) return;
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const i = Math.max(0, rows.findIndex((r) => r.application_id === selected));
      const next = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
      if (next) select(next.application_id);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selected, select, view, drawer]);

  async function changeStatus(s: "new" | "shortlisted") {
    if (!panel) return;
    try { await patch(`/applications/${panel.application_id}`, { status: s }); refresh(); }
    catch (e) { toast.error((e as Error).message); }
  }
  async function changeStage(stageId: string) {
    if (!panel) return;
    try { await patch(`/applications/${panel.application_id}/stage`, { stage_id: stageId }); refresh(); }
    catch (e) { toast.error((e as Error).message); }
  }

  // Start interview loop: the ticked candidates, or the one open in the panel (any status; specs/16 §0).
  const loopIds = picked.length ? picked : panel ? [panel.application_id] : [];
  const loop = useStartLoop(id, refresh);

  return (
    <div className="flex h-[calc(100vh-5.125rem)] min-h-[36rem] flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2">
        <Link href="/recruiter" aria-label="Jobs" className="text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /></Link>
        <div className="min-w-0">
          <h1 className="text-lg leading-tight font-semibold tracking-tight">{job?.title ?? "…"}</h1>
          <p className="text-xs text-muted-foreground">{job?.company}</p>
        </div>
        <div role="group" aria-label="View" className="flex rounded-lg border bg-muted p-0.5 text-sm">
          {(["ranked", "pipeline", "jd"] as const).map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => setUrl({ view: v })}
              className={cn("rounded-md px-3 py-0.5", view === v ? "bg-white shadow-sm" : "text-muted-foreground")}>
              {v === "ranked" ? "Ranked" : v === "pipeline" ? "Pipeline" : "Job description"}
            </button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" disabled={!loopIds.length || loop.busy} onClick={() => loop.start(loopIds)}
            title={picked.length ? "Start for the ticked candidates" : "Start for the candidate shown"}>
            <CalendarClock /> Start interview loop{picked.length ? ` (${picked.length})` : ""}
          </Button>
          <Button size="sm" variant="outline" disabled={picked.length < 2 || picked.length > 3}
            onClick={() => router.push(`/recruiter/jobs/${id}/compare?ids=${picked.join(",")}`)}>
            <Columns3 /> Compare{picked.length ? ` (${picked.length})` : ""}
          </Button>
          <Link href={`/recruiter/jobs/${id}/edit`} className={buttonVariants({ variant: "outline", size: "sm" })}>
            <Pencil /> Edit job
          </Link>
          <Link href={`/recruiter/jobs/${id}/settings`} className={buttonVariants({ variant: "outline", size: "sm" })}>
            <Settings2 /> Job settings
          </Link>
        </div>
      </div>

      {view === "jd" ? (
        <JobDescription jobId={id} />
      ) : view === "pipeline" ? (
        <PipelineBoard jobId={id} />
      ) : rows !== null && rows.length === 0 && !status && !allMust && !minScore ? (
        <p className="rounded-xl border border-dashed bg-white px-4 py-10 text-center text-sm text-muted-foreground">
          No applicants yet.
        </p>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[380px_minmax(0,1fr)] gap-3">
          <section aria-label="Ranked applicants" className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-white">
            <div className="sticky top-0 z-10 flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/40 p-2.5 text-sm">
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
                <option value="in_loop">In interview loop</option>
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
              <ApplicantList key={filterKey} rows={rows} selected={selected} onSelect={select} picked={picked}
                roleTitle={job?.title}
                onPick={(a) => setPicked((p) => (p.includes(a) ? p.filter((x) => x !== a) : [...p, a]))} />
            )}
          </section>
          <section aria-label="Candidate" className="min-h-0 overflow-hidden rounded-xl border bg-white">
            {panel ? (
              <ProfilePanel p={panel} tab={tab} onTab={(t) => setUrl({ tab: t })} stages={stages}
                onStatus={changeStatus} onStage={changeStage} onReject={() => setDrawer("reject")}
                onStageEmail={() => setDrawer("stage")} activityKey={activityKey} />
            ) : <Skeleton className="m-4 h-96" />}
          </section>
        </div>
      )}
      {panel && (
        <>
          <RejectDrawer open={drawer === "reject"} onOpenChange={(o) => setDrawer(o ? "reject" : null)}
            applicationId={panel.application_id} name={panel.name} onSent={refresh} />
          <StageEmailDrawer open={drawer === "stage"} onOpenChange={(o) => setDrawer(o ? "stage" : null)}
            applicationId={panel.application_id} name={panel.name} stages={stages} onSent={refresh} />
        </>
      )}
      {loop.dialog}
    </div>
  );
}
