"use client";
import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { TaskState } from "@/hooks/use-task";

export function ProgressPanel({ title, task }: { title: string; task: TaskState }) {
  const recent = task.events.slice(-8);
  return (
    <Card aria-live="polite">
      <CardHeader className="flex flex-row items-center gap-2 space-y-0">
        {task.status === "error" ? <CircleAlert className="size-5 text-destructive" />
          : task.status === "done" ? <CheckCircle2 className="size-5 text-emerald-600" />
          : <Loader2 className="size-5 animate-spin text-primary" />}
        <CardTitle className="text-base">{title}</CardTitle>
        <span className="ml-auto text-sm tabular-nums text-muted-foreground">{Math.round(task.percent)}%</span>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0}
          aria-valuemax={100} aria-valuenow={Math.round(task.percent)}>
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${task.percent}%` }} />
        </div>
        <ol className="space-y-1 text-sm">
          {recent.map((e) => (
            <li key={e.seq} className={e.status === "error" ? "text-destructive" : "text-muted-foreground"}>
              {e.message}
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
