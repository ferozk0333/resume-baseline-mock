"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, ChevronRight, ClipboardList, Inbox, Mail, MessageSquareReply, Scale, Send } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { get, type RecruiterTask } from "@/lib/api";

const ICON: Record<string, typeof Mail> = { approve_email: Send, confirm_slot: CalendarClock, enter_feedback: ClipboardList,
  decide: Scale, reply: Inbox, confirm_withdrawal: MessageSquareReply, choose: Mail };

const ago = (iso: string) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 60 ? `${Math.max(1, m)} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
};

/** Everything waiting for the recruiter across jobs, oldest first (specs/16 §1). */
export default function TasksPage() {
  const [tasks, setTasks] = useState<RecruiterTask[] | null>(null);
  useEffect(() => {
    const load = () => get<RecruiterTask[]>("/recruiter/tasks").then(setTasks).catch((e) => toast.error(e.message));
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
        <p className="text-sm text-muted-foreground">Everything waiting for you, oldest first. Each opens the candidate&apos;s workflow at that step. Nothing is sent until you approve it.</p>
      </div>
      {tasks === null ? <Skeleton className="h-48" /> : tasks.length === 0 ? (
        <p className="rounded-xl border border-dashed bg-white px-4 py-8 text-center text-sm text-muted-foreground">
          Nothing waiting. Start an interview loop from a job to see steps here.</p>
      ) : (
        <ul aria-label="Tasks" className="divide-y rounded-xl border bg-white">
          {tasks.map((t) => {
            const Icon = ICON[t.kind] ?? Mail;
            return (
              <li key={`${t.application_id}-${t.kind}`}>
                <Link href={`/recruiter/applications/${t.application_id}/workflow`}
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
