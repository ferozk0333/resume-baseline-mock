"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Bot, CalendarClock, CheckCircle2, Circle, CircleDot, Mail, MailOpen, MinusCircle, Pause, Play,
  User, UserRound, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RejectDrawer } from "@/components/app/recruiter/decision-drawers";
import { useStartLoop } from "@/components/app/recruiter/start-loop";
import { ReplyBox, StepCard } from "@/components/app/recruiter/workflow-step";
import { get, post, put, type TimelineItem, type WorkflowView } from "@/lib/api";
import { cn } from "@/lib/utils";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric",
  hour: "2-digit", minute: "2-digit" }) : "");

export default function WorkflowPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Workflow /></Suspense>;
}

/** One application's hiring workflow (specs/16 §2): timeline · current step · thread and history. */
function Workflow() {
  const { id } = useParams<{ id: string }>();
  const [v, setV] = useState<WorkflowView | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reject, setReject] = useState(false);
  const load = useCallback(() => {
    get<WorkflowView>(`/applications/${id}/workflow`).then(setV).catch((e) => toast.error(e.message));
  }, [id]);
  useEffect(() => { load(); }, [load]);
  const loop = useStartLoop(v?.application.job_id ?? "", load);

  async function run(p: () => Promise<WorkflowView>, label = "act") {
    setBusy(label);
    try { setV(await p()); } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  }
  const act = (body: Record<string, unknown>, label?: string) =>
    run(() => post<WorkflowView>(`/applications/${id}/workflow/action`, body), label);

  if (!v) return <Skeleton className="h-96" />;
  const a = v.application;
  const wf = v.workflow;
  const open = wf && wf.status !== "closed";
  return (
    <div className="flex flex-col gap-3 lg:h-[calc(100vh-5.125rem)] lg:min-h-[36rem]">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2">
        <Link href={`/recruiter/jobs/${a.job_id}?app=${a.id}`} aria-label="Back to the job" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0">
          <h1 className="text-lg leading-tight font-semibold tracking-tight">{a.name ?? "Applicant"}</h1>
          <p className="text-xs text-muted-foreground">{a.job_title} · {a.company}</p>
        </div>
        <dl className="flex gap-5 text-sm">
          <div><dt className="text-xs text-muted-foreground">Stage</dt><dd className="font-medium">{wf?.stage ?? a.board_stage ?? "New"}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Match</dt>
            <dd className="font-semibold tabular-nums text-primary">{a.match === null ? "—" : a.match.toFixed(1)}</dd></div>
          {wf && <div><dt className="text-xs text-muted-foreground">Workflow</dt><dd><Badge variant="secondary">{wf.badge?.label ?? wf.status_label}</Badge></dd></div>}
        </dl>
        <div className="ml-auto flex gap-2">
          {open && (wf.status === "paused"
            ? <Button size="sm" variant="outline" onClick={() => act({ action: "resume" })}><Play /> Resume</Button>
            : <Button size="sm" variant="outline" onClick={() => act({ action: "pause" })}><Pause /> Pause</Button>)}
          {open && <Button size="sm" variant="outline" onClick={() => setReject(true)}>Not moving forward</Button>}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[16rem_minmax(0,1fr)_24rem]">
        <nav aria-label="Stage timeline" className="min-h-0 overflow-y-auto rounded-xl border bg-white p-4">
          <Timeline items={v.timeline} />
        </nav>
        <div className="min-h-0 space-y-3 overflow-y-auto">
          {!wf || wf.status === "closed" ? (
            <section aria-label="Current step" className="space-y-3 rounded-xl border bg-white p-5">
              {wf?.status === "closed" ? (
                <p className="text-sm">Workflow closed: <b>{wf.outcome === "hired" ? "hired" : wf.outcome === "withdrew" ? "the candidate withdrew"
                  : wf.outcome === "declined" ? "the candidate declined the offer" : wf.outcome === "closed" ? "closed without an email" : "not moving forward"}</b>.</p>
              ) : <p className="text-sm text-muted-foreground">No interview loop yet for this candidate.</p>}
              <Button size="sm" disabled={loop.busy} onClick={() => loop.start([a.id])}>
                <CalendarClock /> {wf ? "Start a new interview loop" : "Start interview loop"}
              </Button>
            </section>
          ) : wf.status === "paused" ? (
            <section aria-label="Current step" className="rounded-xl border bg-white p-5 text-sm">
              <p><b>Paused</b> at “{v.step?.title}”. Nothing happens until you resume: no nudges, no reminders.</p>
            </section>
          ) : v.step && (
            <StepCard v={v} act={act} busy={busy}
              onSaveEmail={async (eid, subject, body) => {
                try { await put(`/emails/${eid}`, { subject, body }); } catch (e) { toast.error((e as Error).message); throw e; }
              }}
              onReply={(body) => run(() => post<WorkflowView>(`/applications/${id}/workflow/reply`, body), "reply")}
              onScorecard={(body) => run(() => post<WorkflowView>(`/applications/${id}/workflow/scorecards`, body), "card")} />
          )}
        </div>
        <aside aria-label="Thread and history" className="min-h-0 space-y-4 overflow-y-auto rounded-xl border bg-white p-4">
          <Thread v={v} />
          <History v={v} />
        </aside>
      </div>
      <RejectDrawer open={reject} onOpenChange={setReject} applicationId={a.id} name={a.name} onSent={load} />
      {loop.dialog}
    </div>
  );
}

function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="space-y-0.5 text-sm">
      {items.map((t, i) => {
        const header = t.group && t.group !== items[i - 1]?.group ? t.group : null;
        const Icon = t.status === "done" ? CheckCircle2 : t.status === "current" ? CircleDot : t.status === "skipped"
          ? MinusCircle : t.status === "closed" ? XCircle : Circle;
        return (
          <li key={t.key}>
            {header && <div className="mt-2 mb-0.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{header}</div>}
            <div aria-current={t.status === "current" ? "step" : undefined}
              className={cn("flex items-center gap-2 rounded-md px-1.5 py-1", t.group && "pl-3",
                t.status === "current" && "bg-primary/5 font-medium", ["upcoming", "not_reached"].includes(t.status) && "text-muted-foreground")}>
              <Icon className={cn("size-4 shrink-0", t.status === "done" && "text-emerald-600", t.status === "current" && "text-primary",
                t.status === "closed" && "text-red-600", t.status === "skipped" && "text-amber-600")} />
              <span className="min-w-0 flex-1 truncate" title={t.note ?? undefined}>{t.name}{t.status === "skipped" && " (skipped)"}</span>
              {t.date && <span className="shrink-0 text-[11px] text-muted-foreground tabular-nums" title={when(t.date)}>
                {new Date(t.date).toLocaleDateString([], { month: "short", day: "numeric" })}</span>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Thread({ v }: { v: WorkflowView }) {
  return (
    <section aria-label="Thread">
      <h2 className="mb-2 text-sm font-semibold">Thread</h2>
      {v.thread.length === 0 && <p className="text-xs text-muted-foreground">No emails yet.</p>}
      <ol className="space-y-2">
        {v.thread.map((m) => m.direction === "in" && m.reading ? <li key={m.id}><ReplyBox m={m} /></li> : (
          <li key={m.id} className={cn("rounded-lg border p-2.5 text-sm", m.direction === "in" && "bg-amber-50/60")}>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {m.direction === "out" ? <Mail className="size-3.5" /> : <MailOpen className="size-3.5" />}
              {m.direction === "out" ? `Sent to ${m.actual_to} (${m.source})` : `Received (${m.source})${m.status === "new" ? " · new" : ""}`}
              <span className="ml-auto">{when(m.sent_at ?? m.created_at)}</span>
            </div>
            <details>
              <summary className="cursor-pointer font-medium">{m.subject}</summary>
              <pre className="mt-1 font-[inherit] text-xs whitespace-pre-wrap">{m.body}</pre>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}

const ACTOR = { recruiter: User, agent: Bot, candidate: UserRound };

function History({ v }: { v: WorkflowView }) {
  return (
    <section aria-label="History">
      <h2 className="mb-2 text-sm font-semibold">History</h2>
      <ol className="space-y-1.5 text-xs">
        {[...v.history].reverse().map((h, i) => {
          const Icon = ACTOR[h.actor] ?? Bot;
          return (
            <li key={i} className="flex gap-2">
              <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-label={h.actor} />
              <span className="min-w-0 flex-1">{h.text}</span>
              <span className="shrink-0 text-muted-foreground tabular-nums">{when(h.at)}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
