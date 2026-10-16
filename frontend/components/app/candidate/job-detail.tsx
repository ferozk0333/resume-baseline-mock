"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bookmark, BookmarkCheck, Briefcase, CircleCheck, MapPin } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { get, post, postedAgo, salaryText, WORKPLACE, type JobActivity, type JobDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

/** "Preview" badge on every JobMatch number (specs/17 §1, §6). */
export function PreviewBadge() {
  return (
    <Tooltip>
      <TooltipTrigger render={<button type="button" className="rounded-full border border-amber-300 bg-amber-50 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-amber-800" />}>
        Preview
      </TooltipTrigger>
      <TooltipContent>Sample value. JobMatch scoring is coming soon.</TooltipContent>
    </Tooltip>
  );
}

export function ActivityLine({ a }: { a: JobActivity }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={a.low ? "text-muted-foreground" : "text-emerald-800"}>{a.label}</span>
      {a.reviewing && <Badge variant="outline" className="border-emerald-300 text-emerald-800">Reviewing applications</Badge>}
    </span>
  );
}

/** Job detail (specs/17 §5). Code only on the server: no model call to open it, read it or apply. */
export function JobDetailView({ cid, jobId, onChanged }: { cid: string; jobId: string; onChanged?: () => void }) {
  const [d, setD] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState(false);
  const load = useCallback(() => {
    get<JobDetail>(`/candidates/${cid}/jobs/${jobId}`).then((x) => { setD(x); setError(null); })
      .catch((e) => setError(e.message));
  }, [cid, jobId]);
  useEffect(() => { load(); }, [load]);

  if (error) return <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
    This job could not be loaded: {error} <button type="button" className="font-medium underline" onClick={load}>Try again</button></p>;
  if (!d || d.job.id !== jobId) return <Skeleton className="h-96" />;
  const j = d.job;
  const facts = [j.location, j.workplace ? WORKPLACE[j.workplace] : null, j.employment_type].filter(Boolean).join(" · ");
  const pay = salaryText(j.salary);

  async function toggleSave() {
    try { await post(`/candidates/${cid}/jobs/${jobId}/save`, { saved: !d!.saved }); load(); onChanged?.(); }
    catch (e) { toast.error((e as Error).message); }
  }

  return (
    <article aria-label={`Job: ${j.title}`} className="space-y-6">
      {/* 1. Header */}
      <header className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight">{j.title}</h2>
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-1"><Briefcase className="size-3.5" aria-hidden />{j.company}</span>
              {facts && <span className="inline-flex items-center gap-1"><MapPin className="size-3.5" aria-hidden />{facts}</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={toggleSave} aria-pressed={d.saved}>
              {d.saved ? <BookmarkCheck /> : <Bookmark />} {d.saved ? "Saved" : "Save"}</Button>
            {d.applied ? <Badge className="gap-1 py-1"><CircleCheck className="size-3.5" /> {d.applied.status}</Badge>
              : <Button onClick={() => setSheet(true)} disabled={j.status !== "open"}>Apply</Button>}
          </div>
        </div>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className={pay ? "font-medium" : "text-muted-foreground"}>{pay ?? "Salary not listed"}</span>
          <span className="text-muted-foreground">{postedAgo(j.created_at)}</span>
          <ActivityLine a={d.activity} />
        </p>
        {!d.profile_confirmed && !d.applied && (
          <p role="note" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Applying uses your confirmed profile. <Link href="/onboarding" className="font-medium underline">Finish reviewing your profile</Link> first.
          </p>
        )}
      </header>

      {/* 2. JobMatch (Preview) and, right below, the baseline score: two different scores */}
      <section aria-label="JobMatch" className="rounded-xl border bg-white p-4">
        {d.jobmatch ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">JobMatch <PreviewBadge /></div>
              <div className="text-3xl font-semibold tabular-nums">{d.jobmatch.value}</div>
            </div>
            {d.jobmatch.must_haves_total > 0 && (
              <div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">Must-haves <PreviewBadge /></div>
                <div className="text-lg font-medium tabular-nums">{d.jobmatch.must_haves_met}/{d.jobmatch.must_haves_total}</div>
              </div>
            )}
            <p className="min-w-0 flex-1 basis-60 text-sm text-muted-foreground">
              Sample value. JobMatch compares your evidence to this job&apos;s requirements and is coming soon.</p>
          </div>
        ) : <p className="text-sm text-muted-foreground">JobMatch is coming soon.</p>}
        <p className="mt-3 border-t pt-3 text-sm" data-testid="baseline-under-jobmatch">
          {d.role.title ? (d.role.baseline_score !== null
            ? <>Your baseline score for {d.role.title}: <span className="font-semibold tabular-nums">{d.role.baseline_score.toFixed(0)}</span>
              <span className="text-muted-foreground"> (your average standing for the role, not this job)</span></>
            : <>No baseline score for {d.role.title} yet. <Link href="/scores" className="font-medium text-primary hover:underline">Add the role in My scores</Link></>)
            : <span className="text-muted-foreground">This job doesn&apos;t match one of the roles we score yet.</span>}
        </p>
      </section>

      {/* 3. Requirements */}
      <section className="space-y-2">
        <h3 className="font-semibold">Requirements</h3>
        <ul className="divide-y rounded-xl border bg-white">
          {d.requirements.map((r) => (
            <li key={r.name} className="space-y-1.5 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{r.name}</span>
                {r.must_have && <Badge>Must-have</Badge>}
                <Badge variant={r.priority === "required" ? "secondary" : "outline"}>{r.priority === "required" ? "Required" : "Preferred"}</Badge>
              </div>
              {r.evidence ? (
                <div className="rounded-md bg-muted/60 px-3 py-2">
                  <div className="text-xs font-medium text-muted-foreground">Your closest evidence · {r.evidence.caption}</div>
                  <div>{r.evidence.text}{r.evidence.matched !== r.name && <span className="text-muted-foreground"> (as {r.evidence.matched})</span>}</div>
                  {r.evidence.quote && <blockquote className="mt-1 border-l-2 pl-2 text-muted-foreground">“{r.evidence.quote}”
                    {r.evidence.source && <span className="not-italic"> · {r.evidence.source}</span>}</blockquote>}
                </div>
              ) : <p className="text-muted-foreground">Checked when JobMatch launches</p>}
            </li>
          ))}
        </ul>
      </section>

      {/* 4. What would raise your match */}
      <section className="space-y-2">
        <h3 className="font-semibold">What would raise your match</h3>
        {d.raise.length > 0 ? (
          <>
            {d.raise_fallback && <p className="text-sm text-muted-foreground">None of your plan&apos;s actions name this job&apos;s requirements; your top action:</p>}
            <ol className="space-y-2">
              {d.raise.map((a, i) => (
                <li key={i} className="rounded-xl border bg-white px-4 py-3 text-sm">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <span className="font-medium">{a.title}</span>
                    <span className="font-medium text-primary">up to {a.points.toFixed(1)} points</span>
                  </div>
                  <p className="mt-1 text-muted-foreground">{a.what_to_do}</p>
                </li>
              ))}
            </ol>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            {d.role.run_id ? <>Your plan for {d.role.title} is prepared when you open it. <Link
              href={`/scores?role=${d.role.baseline_id}&tab=plan`} className="font-medium text-primary hover:underline">Open the plan</Link></>
              : "Add this role in My scores to get a plan for it."}
          </p>
        )}
      </section>

      {/* 5. About the job */}
      <section className="space-y-2 text-sm">
        <h3 className="font-semibold">About the job</h3>
        {j.short_description && <p>{j.short_description}</p>}
        {j.responsibilities?.length > 0 && (
          <div><h4 className="font-medium">What you&apos;ll do</h4>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{j.responsibilities.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
        )}
        <p className="text-muted-foreground">{[j.department, j.level ? `${j.level[0].toUpperCase()}${j.level.slice(1)} level` : null,
          j.min_years !== null && j.min_years !== undefined ? `${j.min_years}+ years` : null].filter(Boolean).join(" · ")}</p>
      </section>

      <ApplySheet open={sheet} onOpenChange={setSheet} cid={cid} d={d} onApplied={() => { load(); onChanged?.(); }} />
    </article>
  );
}

/** "What the recruiter will see" (specs/17 §5.6). Apply runs the existing apply logic unchanged. */
function ApplySheet({ open, onOpenChange, cid, d, onApplied }: { open: boolean; onOpenChange: (o: boolean) => void;
  cid: string; d: JobDetail; onApplied: () => void }) {
  const [upToDate, setUpToDate] = useState(false);
  const [busy, setBusy] = useState(false);
  const v = d.recruiter_view;
  async function apply() {
    setBusy(true);
    try {
      await post(`/candidates/${cid}/apply`, { job_id: d.job.id });
      toast.success("Applied. The recruiter can see your application now.");
      onOpenChange(false);
      onApplied();
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <Sheet open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setUpToDate(false); }}>
      <SheetContent className="w-full gap-0 overflow-y-auto data-[side=right]:sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>What the recruiter will see</SheetTitle>
          <SheetDescription>Applying to {d.job.title} at {d.job.company}.</SheetDescription>
        </SheetHeader>
        <div className="space-y-4 p-4 text-sm">
          <section><h4 className="font-medium">Your proof</h4>
            <ul className="mt-1 list-disc pl-5 text-muted-foreground">{v.sources.map((s) => <li key={s}>{s}</li>)}</ul></section>
          {v.roles.length > 0 && <section><h4 className="font-medium">Roles</h4>
            <ul className="mt-1 list-disc pl-5 text-muted-foreground">{v.roles.map((s) => <li key={s}>{s}</li>)}</ul></section>}
          {v.projects.length > 0 && <section><h4 className="font-medium">Projects</h4>
            <ul className="mt-1 list-disc pl-5 text-muted-foreground">{v.projects.map((s) => <li key={s}>{s}</li>)}</ul></section>}
          {v.top_evidence.length > 0 && <section><h4 className="font-medium">Top evidence</h4>
            <ul className="mt-1 space-y-1.5">{v.top_evidence.map((e) => (
              <li key={e.name} className="rounded-md bg-muted/60 px-2.5 py-1.5"><span className="font-medium">{e.name}</span>
                <span className="text-muted-foreground">: “{e.quote}”</span></li>))}</ul></section>}
          <p className="rounded-md bg-sky-50 px-3 py-2 text-sky-900">
            The recruiter scores your application against their own job. They don&apos;t see your baseline score or the JobMatch preview.</p>
          {!d.profile_confirmed && (
            <p role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-amber-900">
              You can apply once your profile is confirmed. <Link href="/onboarding" className="font-medium underline">Review your profile</Link></p>
          )}
          <label className={cn("flex items-center gap-2", !d.profile_confirmed && "opacity-50")}>
            <input type="checkbox" checked={upToDate} disabled={!d.profile_confirmed} onChange={(e) => setUpToDate(e.target.checked)} />
            My profile is up to date
          </label>
        </div>
        <SheetFooter className="border-t">
          <div className="flex justify-end gap-2">
            <Link href="/profile" className={buttonVariants({ variant: "outline" })}>Update profile</Link>
            <Button onClick={apply} disabled={!upToDate || busy || !d.profile_confirmed}>{busy ? "Applying…" : "Apply"}</Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
