"use client";
import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, FileText, FolderGit2, Globe, IdCard, Loader2, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { DIMENSIONS, get, post, titleCase, type ReadingProgress, type ReadingSource, type ScoringProgress } from "@/lib/api";
import { cn } from "@/lib/utils";

const POLL_MS = 800;
export const ADVANCE_MS = 1500;

/** Polls `path` until `stop(data)`; returns the latest answer. */
function usePoll<T>(path: string, stop: (d: T) => boolean, onData?: (d: T) => void) {
  const [data, setData] = useState<T | null>(null);
  const cb = useRef({ stop, onData });
  useEffect(() => { cb.current = { stop, onData }; });
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => get<T>(path).then((d) => {
      if (!alive) return;
      setData(d);
      cb.current.onData?.(d);
      if (!cb.current.stop(d)) timer = setTimeout(tick, POLL_MS);
    }).catch(() => { if (alive) timer = setTimeout(tick, POLL_MS * 3); });
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [path]);
  return data;
}

/** Counts up to `to` (instantly with reduced motion). */
function CountUp({ to }: { to: number }) {
  const reduced = useReducedMotion();
  const [n, setN] = useState(reduced ? to : 0);
  useEffect(() => {
    if (reduced) return;
    let raf = 0;
    const start = performance.now();
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / 700);
      setN(Math.round(to * (1 - (1 - p) ** 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [to, reduced]);
  return <span className="tabular-nums">{reduced ? to : n}</span>;
}

function OverallLine({ busy, label, percent, typical, problem = false }: { busy: boolean; label: string; percent: number;
  typical: number | null; problem?: boolean }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {busy ? <Loader2 className="size-4 shrink-0 text-primary motion-safe:animate-spin" aria-hidden />
          : problem ? <CircleAlert className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          : <Check className="size-4 shrink-0 text-emerald-600" aria-hidden />}
        <p className="text-sm font-medium" data-testid="wait-line">{label}</p>
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">{Math.round(percent)}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Overall progress"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
        <div className="h-full rounded-full bg-primary motion-safe:transition-[width] motion-safe:duration-300 motion-safe:ease-out"
          style={{ width: `${percent}%` }} />
      </div>
      {typical && busy && <p className="text-xs text-muted-foreground">Usually takes about {typical} minute{typical > 1 ? "s" : ""}.</p>}
    </div>
  );
}

function Status({ status, labels }: { status: string; labels: Record<string, string> }) {
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium",
      status === "done" || status === "ready" ? "text-emerald-700" : status === "failed" ? "text-destructive"
        : status === "waiting" ? "text-muted-foreground" : "text-primary")}>
      {(status === "done" || status === "ready") && <Check className="size-3.5 motion-safe:animate-pop" aria-hidden />}
      {status === "failed" && <CircleAlert className="size-3.5" aria-hidden />}
      {labels[status]}
    </span>
  );
}

// --- Reading (step 2) ---------------------------------------------------------------------------------------------

const SOURCE_ICON: Record<ReadingSource["type"], LucideIcon> = { resume: FileText, linkedin_pdf: IdCard, github: FolderGit2,
  portfolio: Globe };
const READ_LABELS = { waiting: "Waiting", reading: "Reading", done: "Done", failed: "Couldn't read" };

/** The document-reading wait: one row per source added, what was found in it, and one stage line. */
export function ReadingWait({ cid, readTaskId, onDone }: { cid: string; readTaskId: string | null; onDone: () => void }) {
  const [said, setSaid] = useState("");
  const seen = useRef<Record<string, string>>({});
  const doneOnce = useRef(false);
  const data = usePoll<ReadingProgress>(`/candidates/${cid}/reading-progress?task=${readTaskId ?? ""}`,
    (d) => d.done || !!d.error, (d) => {
      const news: string[] = [];
      for (const s of d.sources) {
        if (seen.current[s.type] !== s.status && (s.status === "done" || s.status === "failed"))
          news.push(s.status === "done" ? `${s.label} read` : `${s.label} couldn't be read`);
        seen.current[s.type] = s.status;
      }
      if (d.done) news.push("Your documents are read");
      if (news.length) setSaid(news.join(". "));
      if ((d.done || d.error) && !doneOnce.current) { doneOnce.current = true; onDone(); }
    });
  if (!data) return <div className="h-40 rounded-xl border bg-white" aria-busy="true" />;
  return (
    <section aria-label="Reading your documents" className="space-y-4 rounded-xl border bg-white p-4">
      <OverallLine busy={!data.done && !data.error} problem={!!data.error}
        label={data.error ? "We couldn't read your documents" : data.stage_label}
        percent={data.percent} typical={data.typical_minutes} />
      <ul className="space-y-2.5">
        {data.sources.map((s) => {
          const Icon = SOURCE_ICON[s.type];
          return (
            <li key={s.type} data-testid={`source-${s.type}`} className="flex gap-3">
              <span className={cn("mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border",
                s.status === "reading" && "border-primary/40 text-primary motion-safe:animate-pulse",
                s.status === "done" && "border-emerald-300 text-emerald-700", s.status === "failed" && "border-destructive/40 text-destructive",
                s.status === "waiting" && "text-muted-foreground")}>
                <Icon className="size-3.5" aria-hidden /></span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{s.label}</span>
                  <Status status={s.status} labels={READ_LABELS} />
                </div>
                {s.status === "done" && s.counts && (
                  <p className="text-xs text-muted-foreground" data-testid={`counts-${s.type}`}>
                    <CountUp to={s.counts.roles} /> role{s.counts.roles === 1 ? "" : "s"} · <CountUp to={s.counts.projects} />{" "}
                    project{s.counts.projects === 1 ? "" : "s"} · <CountUp to={s.counts.skills} /> skill{s.counts.skills === 1 ? "" : "s"}</p>
                )}
                {s.status === "failed" && s.error && <p className="text-xs text-destructive">{s.error}</p>}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="sr-only" aria-live="polite">{said}</p>
    </section>
  );
}

// --- Scoring (step 4) ----------------------------------------------------------------------------------------------

const SCORE_LABELS = { waiting: "Waiting", scoring: "Scoring", ready: "Ready", failed: "Couldn't finish" };

/** The scoring wait: one row per role (primary first) with four dimension segments; "Your scores are ready", then
 * the results (button, or by itself after 1.5 s). The score itself is revealed on the results screen. */
export function ScoringWait({ cid, onAdvance }: { cid: string; onAdvance: () => void }) {
  const reduced = useReducedMotion();
  const [said, setSaid] = useState("");
  const [nonce, setNonce] = useState(0);
  const seen = useRef<Record<string, string>>({});
  const data = usePoll<ScoringProgress>(`/candidates/${cid}/scoring-progress?n=${nonce}`,
    (d) => d.all_ready || (!d.running && d.roles.every((r) => r.status === "ready" || r.status === "failed")), (d) => {
      const news: string[] = [];
      for (const r of d.roles) {
        if (seen.current[r.baseline_id] !== r.status && (r.status === "ready" || r.status === "failed"))
          news.push(r.status === "ready" ? `${r.title} ready` : `${r.title} couldn't finish`);
        seen.current[r.baseline_id] = r.status;
      }
      if (d.all_ready) news.push("Your scores are ready");
      if (news.length) setSaid(news.join(". "));
    });
  const ready = !!data?.all_ready;
  useEffect(() => {
    if (!ready) return;
    const t = setTimeout(onAdvance, ADVANCE_MS);
    return () => clearTimeout(t);
  }, [ready, onAdvance]);

  async function retry(id: string) {
    try { await post(`/candidates/${cid}/baseline-scores`, { baseline_ids: [id] }); setNonce((n) => n + 1); }
    catch (e) { toast.error((e as Error).message); }
  }

  if (!data) return <div className="h-40 rounded-xl border bg-white" aria-busy="true" />;
  const someReady = data.roles.some((r) => r.status === "ready");
  const stuck = !data.running && !ready;
  return (
    <section aria-label="Scoring your profile" className="space-y-4 rounded-xl border bg-white p-4">
      <OverallLine busy={!ready && !stuck} problem={stuck} label={data.line} percent={data.overall * 100} typical={data.typical_minutes} />
      <ul className="space-y-4">
        {data.roles.map((r) => (
          <li key={r.baseline_id} data-testid={`role-${r.baseline_id}`} className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium">{r.title}{r.is_primary && <span className="ml-1.5 text-xs font-normal text-muted-foreground">primary</span>}</span>
              <Status status={r.status} labels={SCORE_LABELS} />
            </div>
            <div className="grid grid-cols-4 gap-1.5">
              {DIMENSIONS.map((d) => {
                const v = r.dimensions[d] ?? 0;
                const active = r.status === "scoring" && v > 0 && v < 1;
                return (
                  <div key={d} className="min-w-0">
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="meter" aria-label={`${r.title}: ${titleCase(d)}`}
                      aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)}>
                      <div data-testid={`fill-${r.baseline_id}-${d}`}
                        className={cn("h-full rounded-full", r.status === "failed" ? "bg-muted-foreground/40" : r.status === "ready" ? "bg-emerald-500" : "bg-primary",
                          "motion-safe:transition-[width] motion-safe:duration-300 motion-safe:ease-out", active && "motion-safe:animate-pulse")}
                        style={{ width: `${v * 100}%` }} />
                    </div>
                    <div className="mt-1 truncate text-[11px] text-muted-foreground">{titleCase(d)}</div>
                  </div>
                );
              })}
            </div>
            {r.status === "failed" && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-destructive/5 px-3 py-2 text-sm">
                <span>Couldn&apos;t finish this role. We&apos;ll try again.</span>
                <Button size="sm" variant="outline" onClick={() => retry(r.baseline_id)}>Retry</Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {ready ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-700">
            <Check className={cn("size-4", !reduced && "animate-pop")} aria-hidden /> Your scores are ready</span>
          <Button onClick={onAdvance}>See my scores</Button>
        </div>
      ) : stuck && someReady && (
        <div className="flex justify-end border-t pt-3">
          <Button variant="outline" onClick={onAdvance}>See the scores that are ready</Button>
        </div>
      )}
      <p className="sr-only" aria-live="polite">{said}</p>
    </section>
  );
}
