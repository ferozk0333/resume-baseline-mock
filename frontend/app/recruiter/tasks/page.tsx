"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, ChevronRight, ClipboardList, Hourglass, Inbox as InboxIcon, Mail, MessageSquareReply, Scale, Send } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { get, post, type RecruiterTask } from "@/lib/api";

type Inbox = { enabled: boolean; last_check: { at: string; failed: boolean } | null };

const ICON: Record<string, typeof Mail> = { approve_email: Send, confirm_slot: CalendarClock, enter_feedback: ClipboardList,
  decide: Scale, reply: InboxIcon, confirm_withdrawal: MessageSquareReply, choose: Mail, closure: Hourglass };

const ago = (iso: string) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 60 ? `${Math.max(1, m)} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
};

/** Everything waiting for the recruiter across jobs, oldest first (specs/16 §1). */
export default function TasksPage() {
  const [tasks, setTasks] = useState<RecruiterTask[] | null>(null);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [checking, setChecking] = useState(false);
  async function checkNow() {
    setChecking(true);
    try {
      await post("/workflows/tick");
      setTasks(await get<RecruiterTask[]>("/recruiter/tasks"));
      setInbox(await get<Inbox>("/workflows/inbox"));
    } catch (e) { toast.error((e as Error).message); } finally { setChecking(false); }
  }
  useEffect(() => {
    const load = () => {
      get<RecruiterTask[]>("/recruiter/tasks").then(setTasks).catch((e) => toast.error(e.message));
      get<Inbox>("/workflows/inbox").then(setInbox).catch(() => {});
    };
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
        {inbox?.enabled && (
          <Tooltip>
            <TooltipTrigger render={<Button size="xs" variant="outline" disabled={checking} onClick={checkNow} />}>
              <InboxIcon /> {checking ? "Checking…" : "Check inbox now"}
            </TooltipTrigger>
            <TooltipContent>
              {inbox.last_check ? `${inbox.last_check.failed ? "Last check failed" : "Last checked"} at ${new Date(inbox.last_check.at).toLocaleTimeString()}`
                : "Not checked yet"}
            </TooltipContent>
          </Tooltip>
        )}
      </div>
      {tasks === null ? <Skeleton className="h-48" /> : tasks.length === 0 ? (
        <p className="rounded-xl border border-dashed bg-white px-4 py-8 text-center text-sm text-muted-foreground">
          Nothing waiting.</p>
      ) : (
        <ul aria-label="Tasks" className="divide-y rounded-xl border bg-white">
          {tasks.map((t) => {
            const Icon = ICON[t.kind] ?? Mail;
            return (
              <li key={`${t.application_id}-${t.kind}`}>
                <Link href={t.kind === "closure" ? `/recruiter/jobs/${t.job_id}?app=${t.application_id}`   // answer from the job
                  : `/recruiter/applications/${t.application_id}/workflow`}
                  className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50">
                  <Icon className="size-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{t.title}</div>
                    <div className="truncate text-xs text-muted-foreground">{t.job_title} · {t.company} · {t.stage}</div>
                  </div>
                  <Badge variant="secondary" className="shrink-0">{ago(t.since)}</Badge>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
