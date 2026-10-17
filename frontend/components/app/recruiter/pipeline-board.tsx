"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { get, patch, type PipelineBoard as Board } from "@/lib/api";
import { cn } from "@/lib/utils";

const BADGE: Record<string, string> = { needs_action: "bg-amber-100 text-amber-900", waiting_candidate: "bg-sky-100 text-sky-900",
  scheduled: "bg-emerald-100 text-emerald-900", feedback_due: "bg-violet-100 text-violet-900", paused: "bg-slate-100 text-slate-700" };

/** One column per stage (terminal last). Drag a card to another column, or use its "Move to" menu. Clicking a
 *  card opens its hiring workflow. Moving a card never sends anything (specs/16 §5). */
export function PipelineBoard({ jobId }: { jobId: string }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const load = useCallback(() => {
    get<Board>(`/jobs/${jobId}/pipeline`).then(setBoard).catch((e) => toast.error(e.message));
  }, [jobId]);
  useEffect(() => { load(); }, [load]);

  async function move(appId: string, stageId: string) {
    try { await patch(`/applications/${appId}/stage`, { stage_id: stageId }); load(); }
    catch (e) { toast.error((e as Error).message); }
  }
  if (!board) return <Skeleton className="h-96" />;
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <p className="shrink-0 text-xs text-muted-foreground">
        {board.unstaged} applicant{board.unstaged === 1 ? "" : "s"} not yet in the interview plan (shortlist them in Ranked).
        Moving a card logs it and updates the workflow; it never sends anything.
      </p>
      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto pb-2">
        {board.stages.map((s) => (
          <section key={s.id} aria-label={`${s.name} stage`}
            onDragOver={(e) => { e.preventDefault(); setOver(s.id); }} onDragLeave={() => setOver(null)}
            onDrop={(e) => { e.preventDefault(); setOver(null); const a = e.dataTransfer.getData("text/plain"); if (a) move(a, s.id); }}
            className={cn("flex w-64 shrink-0 flex-col rounded-xl border bg-muted/40",
              s.terminal && "bg-red-50/40", over === s.id && "ring-2 ring-primary/40")}>
            <header className="flex items-center justify-between border-b px-3 py-2 text-sm font-medium">
              {s.name}<Badge variant="secondary" className="tabular-nums">{s.cards.length}</Badge>
            </header>
            <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
              {s.cards.map((c) => (
                <li key={c.application_id} draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", c.application_id)}
                  className="cursor-grab rounded-lg border bg-white p-2.5 text-sm shadow-xs active:cursor-grabbing">
                  <div className="flex items-start justify-between gap-2">
                    <Link href={`/recruiter/applications/${c.application_id}/workflow`} className="min-w-0 text-left font-medium hover:underline">
                      <span className="line-clamp-1">{c.name ?? "Unnamed"}</span>
                    </Link>
                    <span className="font-semibold tabular-nums text-primary">{c.match === null ? "—" : Math.round(c.match)}</span>
                  </div>
                  <div className="line-clamp-1 text-xs text-muted-foreground">{c.current_role ?? "—"}</div>
                  {c.workflow?.round && <div className="mt-0.5 text-[11px] font-medium text-foreground/80">{c.workflow.round}</div>}
                  {c.workflow && (
                    <span className={cn("mt-1 inline-block rounded px-1.5 py-px text-[11px] font-medium", BADGE[c.workflow.status])}>
                      {c.workflow.label}
                    </span>
                  )}
                  <select aria-label={`Move ${c.name ?? "applicant"} to`} value="" onChange={(e) => e.target.value && move(c.application_id, e.target.value)}
                    className="mt-1.5 h-6 w-full rounded-md border border-field-border bg-field px-1 text-xs text-muted-foreground">
                    <option value="">Move to…</option>
                    {board.stages.filter((x) => x.id !== s.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                  </select>
                </li>
              ))}
              {s.cards.length === 0 && <li className="px-1 py-4 text-center text-xs text-muted-foreground">Drop a card here</li>}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
