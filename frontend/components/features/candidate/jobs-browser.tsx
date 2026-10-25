"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Bookmark, BookmarkCheck, ChevronDown, EyeOff } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ActivityLine, JobDetailView, PreviewBadge } from "@/components/features/candidate/job-detail";
import { get, HIDE_REASONS, post, postedAgo, salaryText, WORKPLACE, type HomeJob, type HomeJobs } from "@/lib/api";
import { cn } from "@/lib/utils";

type Filters = { role: string; workplace: string; location: string; salary: boolean; saved: boolean };
const WIDE = "(min-width: 1024px)";
const GROUPS: { key: "strong" | "close" | "stretch"; label: string }[] = [
  { key: "strong", label: "Strong match" }, { key: "close", label: "Close: one step away" }, { key: "stretch", label: "Stretch" }];

/** Jobs for you (specs/17 §4): open recruiter jobs ranked by the JobMatch preview, list left and detail right on
 * desktop; on narrow screens a job opens as its own page. */
export function JobsBrowser({ cid }: { cid: string }) {
  const router = useRouter();
  const search = useSearchParams();
  const [data, setData] = useState<HomeJobs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [f, setF] = useState<Filters | null>(null);
  const [others, setOthers] = useState(false);
  const [hiding, setHiding] = useState<HomeJob | null>(null);
  const [showHidden, setShowHidden] = useState(false);

  const load = useCallback(() => {
    get<HomeJobs>(`/candidates/${cid}/jobs`).then((x) => {
      setData(x);
      setError(null);
      setF((cur) => cur ?? { role: "", workplace: x.defaults.workplace ?? "", location: "", salary: false, saved: false });
    }).catch((e) => setError(e.message));
  }, [cid]);
  useEffect(() => { load(); }, [load]);

  const shown = useMemo(() => (data && f ? data.jobs.filter((j) =>
    (!f.role || (f.role === "other" ? !j.target : j.role_baseline_id === f.role))
    && (!f.workplace || j.workplace === f.workplace)
    && (!f.location || j.location.toLowerCase().includes(f.location.trim().toLowerCase()))
    && (!f.salary || j.salary) && (!f.saved || j.saved)) : []), [data, f]);
  const selected = search.get("job") ?? shown.find((j) => j.target)?.id ?? shown[0]?.id ?? null;

  function open(id: string) {
    let wide = true;
    try { wide = window.matchMedia(WIDE).matches; } catch { /* old browser: keep the pane */ }
    const q = new URLSearchParams(search.toString());
    q.set("job", id);
    if (wide) router.replace(`?${q.toString()}`, { scroll: false }); else router.push(`/jobs/${id}`);
  }
  async function toggleSave(j: HomeJob) {
    try { await post(`/candidates/${cid}/jobs/${j.id}/save`, { saved: !j.saved }); load(); }
    catch (e) { toast.error((e as Error).message); }
  }

  if (error) return <div role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
    Jobs could not be loaded: {error} <button type="button" className="font-medium underline" onClick={load}>Try again</button></div>;
  if (!data || !f) return <Skeleton className="h-96" />;

  const target = shown.filter((j) => j.target);
  const other = shown.filter((j) => !j.target);
  const filtered = !!(f.role || f.workplace || f.location || f.salary || f.saved);

  const list = (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Jobs for you</h2>
        <span className="text-xs text-muted-foreground">{shown.length} of {data.jobs.length} open jobs</span>
      </div>
      <FilterBar f={f} setF={setF} data={data} />
      {!data.profile_confirmed && (
        <p role="note" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Your profile isn&apos;t confirmed yet, so matches and applying are on hold.{" "}
          <Link href="/onboarding" className="font-medium underline">Review your profile</Link></p>
      )}
      {data.jobs.length === 0 ? (
        <Empty title={data.hidden > 0 ? "You've hidden every open job" : "No open jobs right now"}
          text={data.hidden > 0 ? "Hidden jobs stay hidden until you bring them back." : "New jobs appear here as recruiters post them."}>
          {data.hidden > 0 && <Button size="sm" variant="outline" onClick={() => setShowHidden(true)}>Show hidden jobs</Button>}
        </Empty>
      ) : shown.length === 0 ? (
        <Empty title="No jobs match these filters" text="Try fewer filters.">
          <Button size="sm" variant="outline" onClick={() => setF({ role: "", workplace: "", location: "", salary: false, saved: false })}>Clear filters</Button>
        </Empty>
      ) : (
        <>
          {target.length === 0 && f.role !== "other" && (
            <Empty title="No jobs for your roles yet" text={`Nothing open for ${data.targets.map((t) => t.title).join(", ") || "your roles"} right now. Other jobs are below.`} />
          )}
          {data.preview ? GROUPS.map((g) => {
            const rows = target.filter((j) => j.group === g.key);
            return rows.length > 0 && (
              <section key={g.key} aria-label={g.label} className="space-y-2">
                <h3 className="text-sm font-medium text-muted-foreground">{g.label} ({rows.length})</h3>
                <ul className="space-y-2">{rows.map((j) => <Row key={j.id} j={j} active={j.id === selected} onOpen={() => open(j.id)}
                  onSave={() => toggleSave(j)} onHide={() => setHiding(j)} />)}</ul>
              </section>
            );
          }) : target.length > 0 && (
            <ul className="space-y-2">{target.map((j) => <Row key={j.id} j={j} active={j.id === selected} onOpen={() => open(j.id)}
              onSave={() => toggleSave(j)} onHide={() => setHiding(j)} />)}</ul>
          )}
          {other.length > 0 && (
            <section aria-label="Other jobs" className="space-y-2">
              <button type="button" aria-expanded={others || filtered} onClick={() => setOthers(!others)}
                className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground">
                <ChevronDown className={cn("size-4 transition-transform", !(others || filtered) && "-rotate-90")} />
                Other jobs ({other.length})</button>
              {(others || filtered) && <ul className="space-y-2">{other.map((j) => <Row key={j.id} j={j} active={j.id === selected}
                onOpen={() => open(j.id)} onSave={() => toggleSave(j)} onHide={() => setHiding(j)} />)}</ul>}
            </section>
          )}
          {data.hidden > 0 && (
            <button type="button" className="text-xs text-muted-foreground underline" onClick={() => setShowHidden(true)}>
              {data.hidden} hidden job{data.hidden > 1 ? "s" : ""}</button>
          )}
        </>
      )}
    </div>
  );

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,400px)_minmax(0,1fr)]">
      {list}
      <div className="hidden lg:block">
        <div className="sticky top-[calc(var(--header-h)+1rem)] max-h-[calc(100vh-var(--header-h)-2rem)] overflow-y-auto rounded-xl border bg-white p-5">
          {selected ? <JobDetailView cid={cid} jobId={selected} onChanged={load} />
            : <p className="text-sm text-muted-foreground">Pick a job to see the details.</p>}
        </div>
      </div>
      <NotInterested job={hiding} onClose={() => setHiding(null)} onDone={async (reason) => {
        const j = hiding!;
        setHiding(null);
        try {
          await post(`/candidates/${cid}/jobs/${j.id}/hide`, { reason });
          toast.success(`${j.title} is hidden`, { action: { label: "Undo", onClick: () => post(`/candidates/${cid}/jobs/${j.id}/unhide`).then(load) } });
          if (selected === j.id) router.replace("?", { scroll: false });
          load();
        } catch (e) { toast.error((e as Error).message); }
      }} />
      <Dialog open={showHidden} onOpenChange={setShowHidden}>
        <DialogContent>
          <DialogHeader><DialogTitle>Hidden jobs</DialogTitle>
            <DialogDescription>Jobs you marked Not interested.</DialogDescription></DialogHeader>
          <ul className="divide-y text-sm">
            {data.hidden_jobs.map((h) => (
              <li key={h.id} className="flex items-center justify-between gap-3 py-2">
                <span><span className="font-medium">{h.title}</span> <span className="text-muted-foreground">· {h.company}</span></span>
                <Button size="sm" variant="outline" onClick={() => post(`/candidates/${cid}/jobs/${h.id}/unhide`).then(load)
                  .catch((e) => toast.error(e.message))}>Show again</Button>
              </li>
            ))}
            {data.hidden_jobs.length === 0 && <li className="py-2 text-muted-foreground">No hidden jobs.</li>}
          </ul>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Empty({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return (
    <div className="space-y-2 rounded-xl border border-dashed bg-white px-4 py-6 text-center text-sm">
      <p className="font-medium">{title}</p><p className="text-muted-foreground">{text}</p>{children}
    </div>
  );
}

function FilterBar({ f, setF, data }: { f: Filters; setF: (f: Filters) => void; data: HomeJobs }) {
  const sel = "h-8 rounded-lg border border-field-border bg-field px-2 text-sm";
  return (
    <div role="group" aria-label="Filters" className="flex flex-wrap items-center gap-2 text-sm">
      <select aria-label="Role" className={sel} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
        <option value="">All roles</option>
        {data.targets.map((t) => <option key={t.baseline_id} value={t.baseline_id}>{t.title}</option>)}
        <option value="other">Other roles</option>
      </select>
      <select aria-label="Work mode" className={sel} value={f.workplace} onChange={(e) => setF({ ...f, workplace: e.target.value })}>
        <option value="">Any work mode</option>
        {Object.entries(WORKPLACE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <Input aria-label="Location" placeholder="Location" className="h-8 w-32" value={f.location}
        onChange={(e) => setF({ ...f, location: e.target.value })} />
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={f.salary}
        onChange={(e) => setF({ ...f, salary: e.target.checked })} /> Salary listed</label>
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={f.saved}
        onChange={(e) => setF({ ...f, saved: e.target.checked })} /> Saved</label>
    </div>
  );
}

function Row({ j, active, onOpen, onSave, onHide }: { j: HomeJob; active: boolean; onOpen: () => void; onSave: () => void;
  onHide: () => void }) {
  const pay = salaryText(j.salary);
  return (
    <li className={cn("rounded-xl border bg-white transition-colors", active ? "border-primary ring-2 ring-primary/20" : "hover:border-foreground/20")}
      data-testid="job-row">
      <button type="button" onClick={onOpen} aria-current={active ? "true" : undefined} className="block w-full px-4 pt-3 text-left">
        <div className="flex items-start justify-between gap-2">
          <span className="font-medium">{j.title}</span>
          {j.applied && <Badge variant="secondary">{j.applied.status}</Badge>}
        </div>
        <div className="text-sm text-muted-foreground">{[j.company, j.location, j.workplace ? WORKPLACE[j.workplace] : null].filter(Boolean).join(" · ")}</div>
        <div className="mt-1 flex flex-wrap gap-x-3 text-sm">
          <span className={pay ? "" : "text-muted-foreground"}>{pay ?? "Salary not listed"}</span>
          <span className="text-muted-foreground">{postedAgo(j.posted_at)}</span>
        </div>
        <div className="mt-1 text-sm"><ActivityLine a={j.activity} /></div>
      </button>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pb-3 pt-1.5 text-sm">
        {j.jobmatch && <span className="flex items-center gap-1.5">JobMatch <span className="font-semibold tabular-nums">{j.jobmatch.value}</span> <PreviewBadge /></span>}
        {j.jobmatch && j.jobmatch.must_haves_total > 0 && (
          <span className="flex items-center gap-1.5">Must-haves <span className="tabular-nums">{j.jobmatch.must_haves_met}/{j.jobmatch.must_haves_total}</span> <PreviewBadge /></span>)}
        <span className="ml-auto flex gap-1">
          <Button size="icon-sm" variant="ghost" aria-label={j.saved ? `Unsave ${j.title}` : `Save ${j.title}`} aria-pressed={j.saved}
            onClick={onSave}>{j.saved ? <BookmarkCheck className="text-primary" /> : <Bookmark />}</Button>
          <Button size="icon-sm" variant="ghost" aria-label={`Not interested in ${j.title}`} onClick={onHide}><EyeOff /></Button>
        </span>
      </div>
    </li>
  );
}

function NotInterested({ job, onClose, onDone }: { job: HomeJob | null; onClose: () => void; onDone: (reason: string | null) => void }) {
  const [reason, setReason] = useState<string | null>(null);
  return (
    <Dialog open={!!job} onOpenChange={(o) => { if (!o) { onClose(); setReason(null); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Not interested in {job?.title}?</DialogTitle>
          <DialogDescription>A reason is optional.</DialogDescription>
        </DialogHeader>
        <fieldset className="space-y-1.5 text-sm">
          <legend className="mb-1 font-medium">Reason (optional)</legend>
          {HIDE_REASONS.map(([k, label]) => (
            <label key={k} className="flex items-center gap-2">
              <input type="radio" name="hide-reason" checked={reason === k} onChange={() => setReason(k)} /> {label}</label>
          ))}
        </fieldset>
        <DialogFooter>
          <Button variant="outline" onClick={() => { onClose(); setReason(null); }}>Cancel</Button>
          <Button onClick={() => { onDone(reason); setReason(null); }}>Hide job</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
