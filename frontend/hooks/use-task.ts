"use client";
// Follows a background task over Server-Sent Events (GET /tasks/{id}/events).
import { useEffect, useState } from "react";
import { API, type TaskEvent } from "@/lib/api";

export type TaskState = { events: TaskEvent[]; status: "idle" | "running" | "done" | "error"; percent: number;
  message: string | null };

const IDLE: TaskState = { events: [], status: "idle", percent: 0, message: null };
const STARTING: TaskState = { events: [], status: "running", percent: 0, message: "Starting…" };

export function useTask(taskId: string | null, onFinish?: (status: "done" | "error", last: TaskEvent) => void): TaskState {
  // State is tagged with its task, so a new task starts from "Starting…" without resetting state in the effect.
  const [state, setState] = useState<{ taskId: string | null; value: TaskState }>({ taskId: null, value: IDLE });
  useEffect(() => {
    if (!taskId) return;
    const source = new EventSource(`${API}/tasks/${taskId}/events`);
    const handle = (kind: "progress" | "done" | "error") => (e: MessageEvent) => {
      const ev = JSON.parse(e.data) as TaskEvent;
      setState((s) => {
        const prev = s.taskId === taskId ? s.value : STARTING;
        return { taskId, value: { events: [...prev.events, ev], status: kind === "progress" ? "running" : kind,
          percent: ev.percent ?? prev.percent, message: ev.message } };
      });
      if (kind !== "progress") {
        source.close();
        onFinish?.(kind, ev);
      }
    };
    source.addEventListener("progress", handle("progress"));
    source.addEventListener("done", handle("done"));
    source.addEventListener("error", (e) => {
      if ((e as MessageEvent).data) handle("error")(e as MessageEvent);
    });
    return () => source.close();
    // onFinish is intentionally not a dependency: one subscription per task
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);
  if (!taskId) return IDLE;
  return state.taskId === taskId ? state.value : STARTING;
}
