"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { get, type MyApplication } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";
import { cn } from "@/lib/utils";

export default function ApplicationsPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Applications /></Suspense>;
}

const CHIP: Record<MyApplication["status"], string> = {
  Applied: "bg-muted text-foreground", "Viewed by recruiter": "bg-sky-50 text-sky-900 border-sky-200",
  Shortlisted: "bg-indigo-50 text-indigo-900 border-indigo-200", Interview: "bg-violet-50 text-violet-900 border-violet-200",
  Offer: "bg-amber-50 text-amber-900 border-amber-200", Hired: "bg-emerald-50 text-emerald-900 border-emerald-200",
  Declined: "bg-muted text-muted-foreground", "Not moving forward": "bg-rose-50 text-rose-900 border-rose-200" };
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "");

/** My applications (specs/17 §7): a status and a timeline per application. Never scores, ranks, other
 * candidates, recruiter notes, scorecards or internal tasks. */
function Applications() {
  const router = useRouter();
  const [cid, ready] = useCurrentCandidate();
  const [apps, setApps] = useState<MyApplication[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!cid) return;
    get<MyApplication[]>(`/candidates/${cid}/applications`).then((a) => { setApps(a); setError(null); })
      .catch((e) => setError(e.message));
  }, [cid]);
  useEffect(() => {
    if (!ready) return;
    if (!cid) router.replace("/onboarding"); else load();
  }, [ready, cid, router, load]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">My applications</h1>
        <p className="text-sm text-muted-foreground">Every application gets an answer. Here is where each one stands.</p>
      </div>
      {error ? (
        <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Your applications could not be loaded: {error} <button type="button" className="font-medium underline" onClick={load}>Try again</button></p>
      ) : apps === null ? <Skeleton className="h-48" /> : apps.length === 0 ? (
        <div className="space-y-3 rounded-xl border border-dashed bg-white px-4 py-8 text-center text-sm">
          <p className="font-medium">No applications yet</p>
          <p className="text-muted-foreground">When you apply to a job, you can follow it here.</p>
          <Link href="/jobs" className={buttonVariants({ size: "sm" })}>Find jobs</Link>
        </div>
      ) : (
        <ul aria-label="Applications" className="space-y-3">
          {apps.map((a) => {
            const expanded = open === a.application_id;
            return (
              <li key={a.application_id} className="rounded-xl border bg-white" data-testid="application">
                <button type="button" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : a.application_id)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left">
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{a.title}</span>
                    <span className="block text-sm text-muted-foreground">{a.company} · Applied {day(a.applied_at)}</span>
                  </span>
                  <Badge variant="outline" className={cn("border", CHIP[a.status])} data-testid="status-chip">
                    {a.status}{a.round ? ` · ${a.round}` : ""}</Badge>
                  <ChevronDown className={cn("size-4 text-muted-foreground motion-safe:transition-transform", expanded && "rotate-180")} aria-hidden />
                </button>
                {a.waiting && (
                  <p className="mx-4 mb-3 flex items-start gap-2 rounded-md bg-muted px-3 py-2 text-sm">
                    <Clock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />{a.waiting_text}</p>
                )}
                {expanded && (
                  <div className="space-y-4 border-t px-4 py-4">
                    <ol aria-label={`Timeline for ${a.title}`} className="space-y-0">
                      {a.timeline.map((s, i) => (
                        <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0">
                          {i < a.timeline.length - 1 && <span aria-hidden className="absolute left-[7px] top-4 h-full w-px bg-border" />}
                          <span aria-hidden className={cn("relative mt-1 size-[15px] shrink-0 rounded-full border-2",
                            s.state === "done" ? "border-primary bg-primary" : s.state === "current" ? "border-primary bg-white"
                              : "border-muted-foreground/30 bg-white")} />
                          <div className="flex min-w-0 flex-1 flex-wrap justify-between gap-x-3 text-sm">
                            <span className={cn(s.state === "upcoming" || s.state === "not_reached" ? "text-muted-foreground" : "font-medium")}>
                              {s.label}{s.state === "current" && <span className="ml-1.5 text-xs font-normal text-primary">now</span>}
                              {s.state === "not_reached" && <span className="sr-only"> (not reached)</span>}
                            </span>
                            <span className="text-xs text-muted-foreground">{day(s.date)}</span>
                          </div>
                        </li>
                      ))}
                    </ol>
                    {a.status === "Not moving forward" && (
                      <section aria-label="Why" className="space-y-2">
                        <h2 className="text-sm font-medium">What the recruiter told you</h2>
                        {a.reasons.length === 0 ? (
                          <p className="text-sm text-muted-foreground">The recruiter didn&apos;t list specific reasons.</p>
                        ) : (
                          <ul className="space-y-2">
                            {a.reasons.map((r) => (
                              <li key={r.title} className="rounded-md bg-muted/60 px-3 py-2 text-sm">
                                <p className="font-medium">{r.title}</p>
                                {r.detail && <p className="text-muted-foreground">{r.detail}</p>}
                                {r.advice && <p className="mt-1">{r.advice}</p>}
                                {r.link && <Link href={r.link} className="mt-1 inline-block text-sm font-medium text-primary hover:underline">
                                  See this in your plan</Link>}
                              </li>
                            ))}
                          </ul>
                        )}
                      </section>
                    )}
                    <Link href={`/jobs/${a.job_id}`} className="inline-block text-sm font-medium text-primary hover:underline">View the job</Link>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
